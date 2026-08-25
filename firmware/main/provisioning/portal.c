// SPDX-FileCopyrightText: 2026 Mark Phelps
// SPDX-License-Identifier: Apache-2.0

#include "portal.h"

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "bootloader_random.h"
#include "credentials.h"
#include "dns_hijack.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_random.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "lwip/inet.h"
#include "mbedtls/platform_util.h"
#include "wifi.h"

#define BOP_PORTAL_NETWORK_LIMIT 15
#define BOP_PORTAL_REQUEST_LIMIT 512
#define BOP_PORTAL_RESTART_DELAY_US 2000000

static const char *TAG = "bop_portal";
static const char AP_PASSWORD_ALPHABET[] = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
static char captive_portal_uri[] = "http://192.168.4.1/";

extern const char portal_page_start[] asm("_binary_portal_page_html_start");
extern const char portal_page_end[] asm("_binary_portal_page_html_end");

typedef struct {
    char ssid[BOP_WIFI_SSID_CAPACITY];
    int8_t rssi;
    bool secure;
    bool supported;
} portal_network_t;

static portal_network_t networks[BOP_PORTAL_NETWORK_LIMIT];
static size_t network_count;
static esp_netif_t *ap_network_interface;
static httpd_handle_t http_server;
static esp_timer_handle_t restart_timer;
static bool portal_started;

static bool valid_utf8(const uint8_t *value, size_t length)
{
    size_t offset = 0;
    while (offset < length) {
        const uint8_t first = value[offset++];
        if (first <= 0x7F) {
            continue;
        }

        size_t continuation_count;
        uint32_t codepoint;
        uint32_t minimum;
        if (first >= 0xC2 && first <= 0xDF) {
            continuation_count = 1;
            codepoint = first & 0x1F;
            minimum = 0x80;
        } else if (first >= 0xE0 && first <= 0xEF) {
            continuation_count = 2;
            codepoint = first & 0x0F;
            minimum = 0x800;
        } else if (first >= 0xF0 && first <= 0xF4) {
            continuation_count = 3;
            codepoint = first & 0x07;
            minimum = 0x10000;
        } else {
            return false;
        }
        if (offset + continuation_count > length) {
            return false;
        }
        for (size_t index = 0; index < continuation_count; ++index) {
            const uint8_t continuation = value[offset++];
            if ((continuation & 0xC0) != 0x80) {
                return false;
            }
            codepoint = (codepoint << 6) | (continuation & 0x3F);
        }
        if (codepoint < minimum || codepoint > 0x10FFFF
            || (codepoint >= 0xD800 && codepoint <= 0xDFFF)) {
            return false;
        }
    }
    return true;
}

static bool supported_auth_mode(wifi_auth_mode_t auth_mode)
{
    switch (auth_mode) {
    case WIFI_AUTH_OPEN:
    case WIFI_AUTH_WPA2_PSK:
    case WIFI_AUTH_WPA_WPA2_PSK:
    case WIFI_AUTH_WPA3_PSK:
    case WIFI_AUTH_WPA2_WPA3_PSK:
    case WIFI_AUTH_WPA3_EXT_PSK:
    case WIFI_AUTH_WPA3_EXT_PSK_MIXED_MODE:
        return true;
    default:
        return false;
    }
}

static void add_scan_record(const wifi_ap_record_t *record)
{
    const size_t ssid_length = strnlen((const char *)record->ssid, sizeof(record->ssid));
    if (ssid_length == 0 || ssid_length >= BOP_WIFI_SSID_CAPACITY
        || !valid_utf8(record->ssid, ssid_length)) {
        return;
    }
    char ssid[BOP_WIFI_SSID_CAPACITY] = {0};
    memcpy(ssid, record->ssid, ssid_length);

    for (size_t index = 0; index < network_count; ++index) {
        if (strcmp(networks[index].ssid, ssid) == 0) {
            if (record->rssi > networks[index].rssi) {
                networks[index].rssi = record->rssi;
                networks[index].secure = record->authmode != WIFI_AUTH_OPEN;
                networks[index].supported = supported_auth_mode(record->authmode);
            }
            return;
        }
    }

    size_t target = network_count;
    if (network_count == BOP_PORTAL_NETWORK_LIMIT) {
        target = 0;
        for (size_t index = 1; index < network_count; ++index) {
            if (networks[index].rssi < networks[target].rssi) {
                target = index;
            }
        }
        if (record->rssi <= networks[target].rssi) {
            return;
        }
    } else {
        ++network_count;
    }

    memset(&networks[target], 0, sizeof(networks[target]));
    memcpy(networks[target].ssid, ssid, ssid_length);
    networks[target].rssi = record->rssi;
    networks[target].secure = record->authmode != WIFI_AUTH_OPEN;
    networks[target].supported = supported_auth_mode(record->authmode);
}

static int compare_network_rssi(const void *left, const void *right)
{
    const portal_network_t *left_network = left;
    const portal_network_t *right_network = right;
    return (int)right_network->rssi - (int)left_network->rssi;
}

static esp_err_t scan_networks(void)
{
    memset(networks, 0, sizeof(networks));
    network_count = 0;

    const wifi_scan_config_t configuration = {
        .show_hidden = false,
        .scan_type = WIFI_SCAN_TYPE_ACTIVE,
    };
    esp_err_t error = esp_wifi_scan_start(&configuration, true);
    if (error != ESP_OK) {
        return error;
    }

    uint16_t record_count = 0;
    error = esp_wifi_scan_get_ap_num(&record_count);
    if (error != ESP_OK) {
        esp_wifi_clear_ap_list();
        return error;
    }
    if (record_count == 0) {
        esp_wifi_clear_ap_list();
        return ESP_OK;
    }

    wifi_ap_record_t *records = calloc(record_count, sizeof(*records));
    if (records == NULL) {
        esp_wifi_clear_ap_list();
        return ESP_ERR_NO_MEM;
    }
    uint16_t returned_count = record_count;
    error = esp_wifi_scan_get_ap_records(&returned_count, records);
    if (error == ESP_OK) {
        for (uint16_t index = 0; index < returned_count; ++index) {
            add_scan_record(&records[index]);
        }
        qsort(networks, network_count, sizeof(networks[0]), compare_network_rssi);
    }
    free(records);
    esp_wifi_clear_ap_list();
    return error;
}

static bool scanned_network_is_unsupported(const char *ssid)
{
    for (size_t index = 0; index < network_count; ++index) {
        if (strcmp(networks[index].ssid, ssid) == 0) {
            return !networks[index].supported;
        }
    }
    return false;
}

static int hex_value(uint8_t value)
{
    if (value >= '0' && value <= '9') {
        return value - '0';
    }
    if (value >= 'A' && value <= 'F') {
        return value - 'A' + 10;
    }
    if (value >= 'a' && value <= 'f') {
        return value - 'a' + 10;
    }
    return -1;
}

static bool decode_form_value(
    const uint8_t *source, size_t source_length, char *output, size_t capacity)
{
    size_t output_length = 0;
    for (size_t offset = 0; offset < source_length; ++offset) {
        uint8_t value = source[offset];
        if (value == '+') {
            value = ' ';
        } else if (value == '%') {
            if (offset + 2 >= source_length) {
                return false;
            }
            const int high = hex_value(source[++offset]);
            const int low = hex_value(source[++offset]);
            if (high < 0 || low < 0) {
                return false;
            }
            value = (uint8_t)((high << 4) | low);
        }
        if (value == 0 || output_length + 1 >= capacity) {
            return false;
        }
        output[output_length++] = (char)value;
    }
    output[output_length] = '\0';
    return valid_utf8((const uint8_t *)output, output_length);
}

static bool parse_form(
    const uint8_t *body,
    size_t body_length,
    char *ssid,
    size_t ssid_capacity,
    char *password,
    size_t password_capacity)
{
    bool has_ssid = false;
    bool has_password = false;
    size_t offset = 0;
    while (offset < body_length) {
        const size_t field_start = offset;
        while (offset < body_length && body[offset] != '&') {
            ++offset;
        }
        const size_t field_end = offset;
        if (field_end == field_start) {
            return false;
        }

        size_t separator = field_start;
        while (separator < field_end && body[separator] != '=') {
            ++separator;
        }
        if (separator == field_end) {
            return false;
        }
        const uint8_t *value = body + separator + 1;
        const size_t value_length = field_end - separator - 1;
        const size_t key_length = separator - field_start;
        if (key_length == 4 && memcmp(body + field_start, "ssid", 4) == 0) {
            if (has_ssid || !decode_form_value(value, value_length, ssid, ssid_capacity)) {
                return false;
            }
            has_ssid = true;
        } else if (key_length == 8 && memcmp(body + field_start, "password", 8) == 0) {
            if (has_password
                || !decode_form_value(value, value_length, password, password_capacity)) {
                return false;
            }
            has_password = true;
        } else {
            return false;
        }

        if (offset == body_length) {
            break;
        }
        ++offset;
        if (offset == body_length) {
            return false;
        }
    }
    return has_ssid && has_password && ssid[0] != '\0';
}

static esp_err_t receive_request_body(httpd_req_t *request, uint8_t *body, size_t capacity)
{
    if (request->content_len == 0 || request->content_len > BOP_PORTAL_REQUEST_LIMIT
        || request->content_len + 1 > capacity) {
        return ESP_ERR_INVALID_SIZE;
    }

    size_t received = 0;
    while (received < request->content_len) {
        int count = httpd_req_recv(
            request, (char *)body + received, request->content_len - received);
        if (count <= 0) {
            return ESP_FAIL;
        }
        received += (size_t)count;
    }
    body[received] = 0;
    return ESP_OK;
}

static esp_err_t send_json(
    httpd_req_t *request, const char *status, const char *response)
{
    esp_err_t error = httpd_resp_set_status(request, status);
    if (error == ESP_OK) {
        error = httpd_resp_set_type(request, "application/json; charset=utf-8");
    }
    if (error == ESP_OK) {
        error = httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    }
    if (error == ESP_OK) {
        error = httpd_resp_set_hdr(request, "Connection", "close");
    }
    if (error == ESP_OK) {
        error = httpd_resp_send(request, response, HTTPD_RESP_USE_STRLEN);
    }
    return error;
}

static esp_err_t root_get(httpd_req_t *request)
{
    esp_err_t error = httpd_resp_set_type(request, "text/html; charset=utf-8");
    if (error == ESP_OK) {
        error = httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    }
    if (error == ESP_OK) {
        error = httpd_resp_send(
            request, portal_page_start, portal_page_end - portal_page_start);
    }
    return error;
}

static bool append_json_ssid(
    const char *ssid, char *escaped, size_t capacity)
{
    size_t output = 0;
    for (const uint8_t *cursor = (const uint8_t *)ssid; *cursor != 0; ++cursor) {
        if (*cursor == '"' || *cursor == '\\') {
            if (output + 2 >= capacity) {
                return false;
            }
            escaped[output++] = '\\';
            escaped[output++] = (char)*cursor;
        } else if (*cursor < 0x20) {
            if (output + 6 >= capacity) {
                return false;
            }
            int written = snprintf(escaped + output, capacity - output, "\\u%04X", *cursor);
            if (written != 6) {
                return false;
            }
            output += 6;
        } else {
            if (output + 1 >= capacity) {
                return false;
            }
            escaped[output++] = (char)*cursor;
        }
    }
    escaped[output] = '\0';
    return true;
}

static esp_err_t networks_get(httpd_req_t *request)
{
    esp_err_t error = httpd_resp_set_type(request, "application/json; charset=utf-8");
    if (error == ESP_OK) {
        error = httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    }
    if (error == ESP_OK) {
        error = httpd_resp_send_chunk(request, "[", 1);
    }
    for (size_t index = 0; error == ESP_OK && index < network_count; ++index) {
        char escaped_ssid[(BOP_WIFI_SSID_CAPACITY - 1) * 6 + 1];
        char object[320];
        if (!append_json_ssid(networks[index].ssid, escaped_ssid, sizeof(escaped_ssid))) {
            error = ESP_ERR_INVALID_STATE;
            break;
        }
        int length = snprintf(
            object,
            sizeof(object),
            "%s{\"ssid\":\"%s\",\"rssi\":%d,\"secure\":%s,\"supported\":%s}",
            index == 0 ? "" : ",",
            escaped_ssid,
            networks[index].rssi,
            networks[index].secure ? "true" : "false",
            networks[index].supported ? "true" : "false");
        if (length < 0 || (size_t)length >= sizeof(object)) {
            error = ESP_ERR_INVALID_SIZE;
            break;
        }
        error = httpd_resp_send_chunk(request, object, length);
    }
    if (error == ESP_OK) {
        error = httpd_resp_send_chunk(request, "]", 1);
    }
    if (error == ESP_OK) {
        error = httpd_resp_send_chunk(request, NULL, 0);
    }
    return error;
}

static void restart_callback(void *argument)
{
    (void)argument;
    esp_restart();
}

static esp_err_t connect_post(httpd_req_t *request)
{
    uint8_t raw_body[BOP_PORTAL_REQUEST_LIMIT + 1] = {0};
    char ssid[BOP_WIFI_SSID_CAPACITY] = {0};
    char password[BOP_WIFI_PASSWORD_CAPACITY] = {0};
    bop_credentials_t candidate = {0};
    const char *status = "400 Bad Request";
    const char *response = "{\"ok\":false,\"error\":\"invalid request\"}";
    bool restart = false;

    esp_err_t error = receive_request_body(request, raw_body, sizeof(raw_body));
    if (error != ESP_OK
        || !parse_form(
            raw_body,
            request->content_len,
            ssid,
            sizeof(ssid),
            password,
            sizeof(password))) {
        goto cleanup;
    }
    if (scanned_network_is_unsupported(ssid)) {
        status = "422 Unprocessable Entity";
        response = "{\"ok\":false,\"error\":\"unsupported network\"}";
        goto cleanup;
    }

    memcpy(candidate.wifi_ssid, ssid, strlen(ssid));
    memcpy(candidate.wifi_password, password, strlen(password));
    error = bop_wifi_connect_bounded(&candidate);
    if (error != ESP_OK) {
        ESP_LOGW(TAG, "WiFi join failed: %s", esp_err_to_name(error));
        status = "504 Gateway Timeout";
        response = "{\"ok\":false,\"error\":\"connection failed\"}";
        goto cleanup;
    }

    error = bop_credentials_store_wifi(ssid, password);
    if (error != ESP_OK) {
        ESP_LOGE(TAG, "WiFi credential storage failed: %s", esp_err_to_name(error));
        bop_wifi_prepare_portal();
        status = "500 Internal Server Error";
        response = "{\"ok\":false,\"error\":\"credentials not saved\"}";
        goto cleanup;
    }

    ESP_LOGI(TAG, "WiFi credentials saved for SSID '%s'", ssid);
    status = "200 OK";
    response = "{\"ok\":true}";
    restart = true;

cleanup:
    mbedtls_platform_zeroize(raw_body, sizeof(raw_body));
    mbedtls_platform_zeroize(password, sizeof(password));
    mbedtls_platform_zeroize(&candidate, sizeof(candidate));
    error = send_json(request, status, response);
    if (error == ESP_OK && restart) {
        error = esp_timer_start_once(restart_timer, BOP_PORTAL_RESTART_DELAY_US);
    }
    return error;
}

static esp_err_t redirect_get(httpd_req_t *request)
{
    esp_err_t error = httpd_resp_set_status(request, "302 Found");
    if (error == ESP_OK) {
        error = httpd_resp_set_hdr(request, "Location", "/");
    }
    if (error == ESP_OK) {
        error = httpd_resp_send(request, "Redirecting to Bop setup", HTTPD_RESP_USE_STRLEN);
    }
    return error;
}

static esp_err_t not_found(httpd_req_t *request, httpd_err_code_t error_code)
{
    (void)error_code;
    if (request->method != HTTP_GET) {
        return httpd_resp_send_err(request, HTTPD_404_NOT_FOUND, "Not found");
    }
    return redirect_get(request);
}

static esp_err_t start_http_server(void)
{
    httpd_config_t configuration = HTTPD_DEFAULT_CONFIG();
    configuration.max_uri_handlers = 7;
    configuration.stack_size = 8192;
    configuration.lru_purge_enable = true;

    esp_err_t error = httpd_start(&http_server, &configuration);
    if (error != ESP_OK) {
        return error;
    }
    const httpd_uri_t root = {
        .uri = "/",
        .method = HTTP_GET,
        .handler = root_get,
    };
    const httpd_uri_t network_list = {
        .uri = "/networks",
        .method = HTTP_GET,
        .handler = networks_get,
    };
    const httpd_uri_t connect = {
        .uri = "/connect",
        .method = HTTP_POST,
        .handler = connect_post,
    };
    error = httpd_register_uri_handler(http_server, &root);
    if (error == ESP_OK) {
        error = httpd_register_uri_handler(http_server, &network_list);
    }
    if (error == ESP_OK) {
        error = httpd_register_uri_handler(http_server, &connect);
    }
    const char *captive_routes[] = {
        "/generate_204",
        "/hotspot-detect.html",
        "/connecttest.txt",
        "/ncsi.txt",
    };
    for (size_t index = 0;
         error == ESP_OK && index < sizeof(captive_routes) / sizeof(captive_routes[0]);
         ++index) {
        const httpd_uri_t captive_route = {
            .uri = captive_routes[index],
            .method = HTTP_GET,
            .handler = redirect_get,
        };
        error = httpd_register_uri_handler(http_server, &captive_route);
    }
    if (error == ESP_OK) {
        error = httpd_register_err_handler(http_server, HTTPD_404_NOT_FOUND, not_found);
    }
    if (error != ESP_OK) {
        httpd_stop(http_server);
        http_server = NULL;
    }
    return error;
}

static void cleanup_portal_start(void)
{
    if (http_server != NULL) {
        httpd_stop(http_server);
        http_server = NULL;
    }
    if (ap_network_interface != NULL) {
        esp_err_t dhcp_error = esp_netif_dhcps_stop(ap_network_interface);
        if (dhcp_error != ESP_OK
            && dhcp_error != ESP_ERR_ESP_NETIF_DHCP_ALREADY_STOPPED) {
            ESP_LOGW(TAG, "Portal DHCP cleanup failed: %s", esp_err_to_name(dhcp_error));
        }
    }
    esp_err_t wifi_error = bop_wifi_stop_portal_ap();
    if (wifi_error != ESP_OK) {
        ESP_LOGW(TAG, "Portal WiFi cleanup failed: %s", esp_err_to_name(wifi_error));
    }
}

static esp_err_t configure_ap_network_interface(void)
{
    if (ap_network_interface == NULL) {
        ap_network_interface = esp_netif_create_default_wifi_ap();
        if (ap_network_interface == NULL) {
            return ESP_ERR_NO_MEM;
        }
    }

    esp_err_t error = esp_netif_dhcps_stop(ap_network_interface);
    if (error != ESP_OK && error != ESP_ERR_ESP_NETIF_DHCP_ALREADY_STOPPED) {
        return error;
    }
    const esp_netif_ip_info_t address = {
        .ip.addr = ipaddr_addr("192.168.4.1"),
        .netmask.addr = ipaddr_addr("255.255.255.0"),
        .gw.addr = ipaddr_addr("192.168.4.1"),
    };
    error = esp_netif_set_ip_info(ap_network_interface, &address);
    if (error == ESP_OK) {
        error = esp_netif_dhcps_option(
            ap_network_interface,
            ESP_NETIF_OP_SET,
            ESP_NETIF_CAPTIVEPORTAL_URI,
            captive_portal_uri,
            strlen(captive_portal_uri));
    }
    if (error == ESP_OK) {
        error = esp_netif_dhcps_start(ap_network_interface);
    }
    return error;
}

esp_err_t bop_portal_prepare(bop_portal_config_t *configuration)
{
    if (configuration == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(configuration, 0, sizeof(*configuration));

    uint8_t mac_address[6];
    esp_err_t error = esp_read_mac(mac_address, ESP_MAC_WIFI_SOFTAP);
    if (error != ESP_OK) {
        return error;
    }
    int length = snprintf(
        configuration->ap_name,
        sizeof(configuration->ap_name),
        "bop-setup-%02X%02X",
        mac_address[4],
        mac_address[5]);
    if (length < 0 || (size_t)length >= sizeof(configuration->ap_name)) {
        return ESP_ERR_INVALID_SIZE;
    }

    bootloader_random_enable();
    for (size_t index = 0; index < BOP_PORTAL_AP_PASSWORD_CAPACITY - 1; ++index) {
        configuration->ap_password[index] = AP_PASSWORD_ALPHABET[esp_random() & 31];
    }
    bootloader_random_disable();

    length = snprintf(
        configuration->qr_payload,
        sizeof(configuration->qr_payload),
        "WIFI:T:WPA;S:%s;P:%s;;",
        configuration->ap_name,
        configuration->ap_password);
    if (length < 0 || (size_t)length >= sizeof(configuration->qr_payload)) {
        mbedtls_platform_zeroize(configuration, sizeof(*configuration));
        return ESP_ERR_INVALID_SIZE;
    }
    return ESP_OK;
}

esp_err_t bop_portal_start(const bop_portal_config_t *configuration)
{
    if (configuration == NULL
        || strnlen(configuration->ap_name, sizeof(configuration->ap_name))
            == sizeof(configuration->ap_name)
        || strnlen(configuration->ap_password, sizeof(configuration->ap_password))
            == sizeof(configuration->ap_password)
        || configuration->ap_name[0] == '\0'
        || configuration->ap_password[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }
    if (portal_started) {
        return ESP_OK;
    }

    esp_err_t error = bop_wifi_init();
    const bool wifi_initialized = error == ESP_OK;
    if (error == ESP_OK) {
        error = configure_ap_network_interface();
    }
    if (error == ESP_OK) {
        error = bop_wifi_start_portal_ap(
            configuration->ap_name, configuration->ap_password);
    }
    if (error == ESP_OK) {
        error = scan_networks();
    }
    if (error == ESP_OK && restart_timer == NULL) {
        const esp_timer_create_args_t timer_configuration = {
            .callback = restart_callback,
            .name = "bop_portal_restart",
        };
        error = esp_timer_create(&timer_configuration, &restart_timer);
    }
    if (error == ESP_OK) {
        error = start_http_server();
    }
    if (error == ESP_OK) {
        error = bop_dns_hijack_start();
    }
    if (error != ESP_OK && wifi_initialized) {
        cleanup_portal_start();
    }
    if (error == ESP_OK) {
        portal_started = true;
        ESP_LOGI(TAG, "Captive portal started for SSID '%s'", configuration->ap_name);
    }
    return error;
}

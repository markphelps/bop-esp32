// SPDX-FileCopyrightText: 2026 Mark Phelps
// SPDX-License-Identifier: Apache-2.0

#include "dns_hijack.h"

#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"

#define DNS_PORT 53
#define DNS_MAX_PACKET_SIZE 512
#define DNS_RESPONSE_EXTRA_SIZE 16
#define DNS_TASK_CORE 0

static const char *TAG = "bop_dns";
static TaskHandle_t dns_task_handle;

static uint16_t read_u16(const uint8_t *bytes)
{
    return ((uint16_t)bytes[0] << 8) | bytes[1];
}

static void write_u16(uint8_t *bytes, uint16_t value)
{
    bytes[0] = value >> 8;
    bytes[1] = value & 0xFF;
}

static void write_u32(uint8_t *bytes, uint32_t value)
{
    bytes[0] = value >> 24;
    bytes[1] = value >> 16;
    bytes[2] = value >> 8;
    bytes[3] = value & 0xFF;
}

static size_t build_response(
    const uint8_t *request, size_t request_length, uint8_t *response, size_t capacity)
{
    if (request == NULL || response == NULL
        || request_length < 12 || request_length > DNS_MAX_PACKET_SIZE
        || capacity < request_length + DNS_RESPONSE_EXTRA_SIZE) {
        return 0;
    }

    const uint16_t flags = read_u16(request + 2);
    if ((flags & 0x8000) != 0 || (flags & 0x7800) != 0 || (flags & 0x0200) != 0
        || read_u16(request + 4) != 1
        || read_u16(request + 6) != 0
        || read_u16(request + 8) != 0
        || read_u16(request + 10) != 0) {
        return 0;
    }

    size_t offset = 12;
    size_t name_length = 0;
    bool has_label = false;
    while (offset < request_length) {
        const uint8_t label_length = request[offset++];
        if (label_length == 0) {
            break;
        }
        if ((label_length & 0xC0) != 0 || label_length > 63
            || offset + label_length > request_length
            || name_length + label_length + 1 > 254) {
            return 0;
        }
        has_label = true;
        name_length += label_length + 1;
        offset += label_length;
    }
    if (!has_label || offset == 12 || request[offset - 1] != 0
        || offset + 4 != request_length
        || read_u16(request + offset) != 1
        || read_u16(request + offset + 2) != 1) {
        return 0;
    }

    memcpy(response, request, request_length);
    write_u16(response + 2, 0x8400 | (flags & 0x0100));
    write_u16(response + 6, 1);

    offset = request_length;
    response[offset++] = 0xC0;
    response[offset++] = 0x0C;
    write_u16(response + offset, 1);
    offset += 2;
    write_u16(response + offset, 1);
    offset += 2;
    write_u32(response + offset, 60);
    offset += 4;
    write_u16(response + offset, 4);
    offset += 2;
    response[offset++] = 192;
    response[offset++] = 168;
    response[offset++] = 4;
    response[offset++] = 1;
    return offset;
}

static void dns_task(void *argument)
{
    const int socket_descriptor = (int)(intptr_t)argument;
    uint8_t request[DNS_MAX_PACKET_SIZE + 1];
    uint8_t response[DNS_MAX_PACKET_SIZE + DNS_RESPONSE_EXTRA_SIZE];

    while (true) {
        struct sockaddr_storage client_address;
        socklen_t client_length = sizeof(client_address);
        ssize_t received = recvfrom(
            socket_descriptor,
            request,
            sizeof(request),
            0,
            (struct sockaddr *)&client_address,
            &client_length);
        if (received < 0) {
            if (errno != EINTR) {
                ESP_LOGW(TAG, "DNS receive failed: errno %d", errno);
            }
            continue;
        }

        size_t response_length = build_response(
            request, (size_t)received, response, sizeof(response));
        if (response_length == 0) {
            continue;
        }
        if (sendto(
                socket_descriptor,
                response,
                response_length,
                0,
                (struct sockaddr *)&client_address,
                client_length)
            < 0) {
            ESP_LOGW(TAG, "DNS response failed: errno %d", errno);
        }
    }
}

esp_err_t bop_dns_hijack_start(void)
{
    if (dns_task_handle != NULL) {
        return ESP_OK;
    }

    int socket_descriptor = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (socket_descriptor < 0) {
        return ESP_FAIL;
    }
    int reuse_address = 1;
    if (setsockopt(
            socket_descriptor,
            SOL_SOCKET,
            SO_REUSEADDR,
            &reuse_address,
            sizeof(reuse_address))
        < 0) {
        close(socket_descriptor);
        return ESP_FAIL;
    }

    const struct sockaddr_in address = {
        .sin_family = AF_INET,
        .sin_port = htons(DNS_PORT),
        .sin_addr.s_addr = inet_addr("192.168.4.1"),
    };
    if (bind(socket_descriptor, (const struct sockaddr *)&address, sizeof(address)) < 0) {
        close(socket_descriptor);
        return ESP_FAIL;
    }
    if (xTaskCreatePinnedToCore(
            dns_task,
            "bop_dns",
            4096,
            (void *)(intptr_t)socket_descriptor,
            4,
            &dns_task_handle,
            DNS_TASK_CORE)
        != pdPASS) {
        close(socket_descriptor);
        dns_task_handle = NULL;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

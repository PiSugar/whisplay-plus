#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "driver/i2c_master.h"
#include "esp_err.h"

#define M5_UNIT_NFC_I2C_ADDRESS 0x50

typedef struct {
    i2c_master_dev_handle_t device;
    bool online;
    bool card_present;
    uint16_t atqa;
    uint8_t misses;
    uint32_t card_events;
} whisplay_nfc_t;

esp_err_t whisplay_nfc_init(i2c_master_bus_handle_t bus, whisplay_nfc_t *nfc);
void whisplay_nfc_deinit(whisplay_nfc_t *nfc);
bool whisplay_nfc_is_connected(whisplay_nfc_t *nfc);

/* Poll ISO14443-A. Returns true only when a newly presented card is seen. */
bool whisplay_nfc_poll(whisplay_nfc_t *nfc);

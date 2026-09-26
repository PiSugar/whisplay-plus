#include "whisplay_nfc.h"

#include <string.h>

#include "esp_check.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

/* Register space A. */
#define REG_IO_CONFIG_1       0x00
#define REG_IO_CONFIG_2       0x01
#define REG_OPERATION_CONTROL 0x02
#define REG_MODE_DEFINITION   0x03
#define REG_BITRATE           0x04
#define REG_ISO14443A         0x05
#define REG_AUXILIARY         0x0a
#define REG_RECEIVER_1        0x0b
#define REG_RECEIVER_2        0x0c
#define REG_RECEIVER_3        0x0d
#define REG_RECEIVER_4        0x0e
#define REG_NRT_1             0x10
#define REG_MAIN_INTERRUPT    0x1a
#define REG_ERROR_INTERRUPT   0x1c
#define REG_PASSIVE_INTERRUPT 0x1d
#define REG_FIFO_STATUS_1     0x1e
#define REG_TX_DRIVER         0x28
#define REG_AUX_DISPLAY       0x31
#define REG_IDENTITY          0x3f

#define OP_READ_REGISTER 0x40
#define OP_READ_FIFO     0x9f

#define CMD_SET_DEFAULT          0xc1
#define CMD_STOP_ALL_ACTIVITIES  0xc2
#define CMD_TRANSMIT_REQA        0xc6
#define CMD_NFC_INITIAL_FIELD_ON 0xc8
#define CMD_RESET_RX_GAIN        0xd5
#define CMD_ADJUST_REGULATORS    0xd6
#define CMD_CLEAR_FIFO           0xdb
#define CMD_TEST_ACCESS          0xfc

#define IRQ_RXS 0x20
#define IRQ_RXE 0x10
#define IRQ_COL 0x04

#define OP_EN    0x80
#define OP_RX_EN 0x40
#define OP_TX_EN 0x08
#define AUX_OSC_OK 0x10

static const char *TAG = "whisplay-nfc";

static esp_err_t st_read(whisplay_nfc_t *nfc, uint8_t reg, void *data, size_t size)
{
    const uint8_t command = OP_READ_REGISTER | (reg & 0x3f);
    return i2c_master_transmit_receive(nfc->device, &command, 1, data, size, 50);
}

static esp_err_t st_write(whisplay_nfc_t *nfc, uint8_t reg, uint8_t value)
{
    const uint8_t transaction[] = {reg & 0x3f, value};
    return i2c_master_transmit(nfc->device, transaction, sizeof(transaction), 50);
}

static esp_err_t st_write_block(whisplay_nfc_t *nfc, uint8_t reg,
                                const uint8_t *data, size_t size)
{
    uint8_t transaction[9];
    ESP_RETURN_ON_FALSE(size <= sizeof(transaction) - 1, ESP_ERR_INVALID_SIZE,
                        TAG, "register write too long");
    transaction[0] = reg & 0x3f;
    memcpy(transaction + 1, data, size);
    return i2c_master_transmit(nfc->device, transaction, size + 1, 50);
}

static esp_err_t st_command(whisplay_nfc_t *nfc, uint8_t command)
{
    return i2c_master_transmit(nfc->device, &command, 1, 50);
}

static esp_err_t st_modify(whisplay_nfc_t *nfc, uint8_t reg,
                           uint8_t clear, uint8_t set)
{
    uint8_t value = 0;
    ESP_RETURN_ON_ERROR(st_read(nfc, reg, &value, 1), TAG, "read reg %02x", reg);
    value = (value & ~clear) | set;
    return st_write(nfc, reg, value);
}

static void st_clear_interrupts(whisplay_nfc_t *nfc)
{
    uint8_t discard[2];
    /* Read error first: reading MAIN also clears latched error state. */
    (void)st_read(nfc, REG_ERROR_INTERRUPT, discard, 1);
    (void)st_read(nfc, REG_MAIN_INTERRUPT, discard, 2);
    (void)st_read(nfc, REG_PASSIVE_INTERRUPT, discard, 1);
}

static esp_err_t st_enable_oscillator(whisplay_nfc_t *nfc)
{
    ESP_RETURN_ON_ERROR(st_modify(nfc, REG_OPERATION_CONTROL, 0, OP_EN),
                        TAG, "enable oscillator");
    for (int i = 0; i < 50; ++i) {
        uint8_t auxiliary = 0;
        ESP_RETURN_ON_ERROR(st_read(nfc, REG_AUX_DISPLAY, &auxiliary, 1),
                            TAG, "read oscillator state");
        if (auxiliary & AUX_OSC_OK) return ESP_OK;
        vTaskDelay(pdMS_TO_TICKS(1));
    }
    return ESP_ERR_TIMEOUT;
}

esp_err_t whisplay_nfc_init(i2c_master_bus_handle_t bus, whisplay_nfc_t *nfc)
{
    memset(nfc, 0, sizeof(*nfc));
    const i2c_device_config_t config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = M5_UNIT_NFC_I2C_ADDRESS,
        .scl_speed_hz = 400000,
    };
    ESP_RETURN_ON_ERROR(i2c_master_bus_add_device(bus, &config, &nfc->device),
                        TAG, "add ST25R3916");
    vTaskDelay(pdMS_TO_TICKS(50));

    uint8_t identity = 0;
    ESP_RETURN_ON_ERROR(st_read(nfc, REG_IDENTITY, &identity, 1), TAG, "read identity");
    ESP_RETURN_ON_FALSE((identity >> 3) == 5 && (identity & 7) != 0,
                        ESP_ERR_NOT_FOUND, TAG, "unexpected identity 0x%02x", identity);

    ESP_RETURN_ON_ERROR(st_command(nfc, CMD_STOP_ALL_ACTIVITIES), TAG, "stop activity");
    ESP_RETURN_ON_ERROR(st_modify(nfc, REG_OPERATION_CONTROL, OP_TX_EN | OP_RX_EN, 0),
                        TAG, "disable field");
    ESP_RETURN_ON_ERROR(st_command(nfc, CMD_SET_DEFAULT), TAG, "set defaults");
    const uint8_t protection[] = {CMD_TEST_ACCESS, 0x04, 0x10};
    ESP_RETURN_ON_ERROR(i2c_master_transmit(nfc->device, protection,
                                             sizeof(protection), 50),
                        TAG, "overheat protection");

    /* 400 kHz I2C threshold, 3.3 V supply, strong digital outputs. */
    const uint8_t io_config[] = {0x10, 0x84};
    ESP_RETURN_ON_ERROR(st_write_block(nfc, REG_IO_CONFIG_1, io_config,
                                       sizeof(io_config)), TAG, "I/O config");
    ESP_RETURN_ON_ERROR(st_write(nfc, REG_TX_DRIVER, 0xd0), TAG, "TX driver");
    ESP_RETURN_ON_ERROR(st_modify(nfc, REG_IO_CONFIG_1, 0x07, 0x07),
                        TAG, "disable clock output");
    ESP_RETURN_ON_ERROR(st_enable_oscillator(nfc), TAG, "oscillator start");
    ESP_RETURN_ON_ERROR(st_command(nfc, CMD_ADJUST_REGULATORS), TAG, "regulator adjust");
    vTaskDelay(pdMS_TO_TICKS(5));

    /* NFC-A, 106 kbit/s. Values mirror M5Stack's ST25R3916 driver. */
    ESP_RETURN_ON_ERROR(st_write(nfc, REG_MODE_DEFINITION, 0x09), TAG, "NFC-A mode");
    ESP_RETURN_ON_ERROR(st_write(nfc, REG_BITRATE, 0x00), TAG, "NFC-A bitrate");
    ESP_RETURN_ON_ERROR(st_write(nfc, REG_ISO14443A, 0x00), TAG, "NFC-A config");
    ESP_RETURN_ON_ERROR(st_modify(nfc, REG_AUXILIARY, 0x04, 0), TAG, "correlator");
    const uint8_t receiver[] = {0x08, 0x2d, 0xd8, 0x22};
    ESP_RETURN_ON_ERROR(st_write_block(nfc, REG_RECEIVER_1, receiver,
                                       sizeof(receiver)), TAG, "receiver config");
    ESP_RETURN_ON_ERROR(st_command(nfc, CMD_RESET_RX_GAIN), TAG, "reset RX gain");

    st_clear_interrupts(nfc);
    ESP_RETURN_ON_ERROR(st_command(nfc, CMD_NFC_INITIAL_FIELD_ON), TAG, "field on");
    vTaskDelay(pdMS_TO_TICKS(5));
    ESP_RETURN_ON_ERROR(st_modify(nfc, REG_OPERATION_CONTROL, 0, OP_TX_EN | OP_RX_EN),
                        TAG, "enable RF field");

    nfc->online = true;
    ESP_LOGI(TAG, "M5Stack Unit NFC online: ST25R3916 identity=0x%02x", identity);
    return ESP_OK;
}

void whisplay_nfc_deinit(whisplay_nfc_t *nfc)
{
    if (!nfc) return;
    if (nfc->device) {
        /* These writes are best effort: unplugging the module is expected. */
        (void)st_command(nfc, CMD_STOP_ALL_ACTIVITIES);
        (void)i2c_master_bus_rm_device(nfc->device);
    }
    memset(nfc, 0, sizeof(*nfc));
}

bool whisplay_nfc_is_connected(whisplay_nfc_t *nfc)
{
    if (!nfc || !nfc->device) return false;
    uint8_t identity = 0;
    return st_read(nfc, REG_IDENTITY, &identity, 1) == ESP_OK &&
           (identity >> 3) == 5 && (identity & 7) != 0;
}

static bool st_reqa(whisplay_nfc_t *nfc, uint16_t *atqa)
{
    /* 4 ms no-response timeout at fc/64: ceil(.004 * 13.56 MHz / 64). */
    const uint8_t nrt[] = {0x03, 0x50};
    if (st_write_block(nfc, REG_NRT_1, nrt, sizeof(nrt)) != ESP_OK ||
        st_write(nfc, REG_ISO14443A, 0x01) != ESP_OK ||
        st_modify(nfc, REG_AUXILIARY, 0, 0x80) != ESP_OK) {
        return false;
    }
    st_clear_interrupts(nfc);
    if (st_command(nfc, CMD_CLEAR_FIFO) != ESP_OK ||
        st_command(nfc, CMD_TRANSMIT_REQA) != ESP_OK) {
        return false;
    }

    uint8_t seen = 0;
    for (int i = 0; i < 8; ++i) {
        uint8_t main_irq = 0;
        if (st_read(nfc, REG_MAIN_INTERRUPT, &main_irq, 1) != ESP_OK) return false;
        seen |= main_irq;
        if (seen & (IRQ_RXE | IRQ_COL)) break;
        vTaskDelay(pdMS_TO_TICKS(1));
    }
    if (!(seen & (IRQ_RXE | IRQ_RXS | IRQ_COL))) return false;

    uint8_t fifo_status[2] = {0};
    if (st_read(nfc, REG_FIFO_STATUS_1, fifo_status, sizeof(fifo_status)) != ESP_OK) {
        return false;
    }
    const uint16_t bytes = fifo_status[0] | ((fifo_status[1] & 0xc0) << 2);
    if (bytes < 2) return false;

    const uint8_t command = OP_READ_FIFO;
    uint8_t response[2] = {0};
    if (i2c_master_transmit_receive(nfc->device, &command, 1,
                                    response, sizeof(response), 50) != ESP_OK) {
        return false;
    }
    *atqa = (uint16_t)response[0] | ((uint16_t)response[1] << 8);
    return true;
}

bool whisplay_nfc_poll(whisplay_nfc_t *nfc)
{
    if (!nfc || !nfc->online) return false;
    uint16_t atqa = 0;
    const bool detected = st_reqa(nfc, &atqa);
    if (detected) {
        nfc->misses = 0;
        nfc->atqa = atqa;
        if (!nfc->card_present) {
            nfc->card_present = true;
            ++nfc->card_events;
            ESP_LOGI(TAG, "NFC-A card detected, ATQA=0x%04x event=%lu",
                     atqa, (unsigned long)nfc->card_events);
            return true;
        }
    } else if (nfc->card_present && ++nfc->misses >= 12) {
        nfc->card_present = false;
        nfc->misses = 0;
        ESP_LOGI(TAG, "NFC card removed");
    }
    return false;
}

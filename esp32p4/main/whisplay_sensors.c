#include "whisplay_sensors.h"

#include <math.h>
#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define QMI8658B_ADDRESS 0x6b
#define MQC5883P_ADDRESS 0x2c

static const char *TAG = "whisplay-sensors";

static int16_t i16le(const uint8_t *data)
{
    return (int16_t)((uint16_t)data[0] | ((uint16_t)data[1] << 8));
}

static esp_err_t add_device(i2c_master_bus_handle_t bus, uint8_t address,
                            i2c_master_dev_handle_t *device)
{
    const i2c_device_config_t config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = address,
        .scl_speed_hz = 400000,
    };
    return i2c_master_bus_add_device(bus, &config, device);
}

static esp_err_t reg_read(i2c_master_dev_handle_t device, uint8_t reg, void *data, size_t size)
{
    return i2c_master_transmit_receive(device, &reg, 1, data, size, 100);
}

static esp_err_t reg_write(i2c_master_dev_handle_t device, uint8_t reg, uint8_t value)
{
    const uint8_t data[] = {reg, value};
    return i2c_master_transmit(device, data, sizeof(data), 100);
}

esp_err_t whisplay_sensors_init(i2c_master_bus_handle_t bus, whisplay_sensors_t *sensors)
{
    memset(sensors, 0, sizeof(*sensors));

    if (i2c_master_probe(bus, QMI8658B_ADDRESS, 50) == ESP_OK &&
        add_device(bus, QMI8658B_ADDRESS, &sensors->imu) == ESP_OK) {
        uint8_t identity[2] = {0};
        if (reg_read(sensors->imu, 0x00, identity, sizeof(identity)) == ESP_OK && identity[0] == 0x05) {
            sensors->imu_revision = identity[1];
            ESP_ERROR_CHECK_WITHOUT_ABORT(reg_write(sensors->imu, 0x02, 0x60));
            ESP_ERROR_CHECK_WITHOUT_ABORT(reg_write(sensors->imu, 0x03, 0x25)); // +/-8 g, 250 Hz
            ESP_ERROR_CHECK_WITHOUT_ABORT(reg_write(sensors->imu, 0x04, 0x45)); // +/-512 dps, 250 Hz
            ESP_ERROR_CHECK_WITHOUT_ABORT(reg_write(sensors->imu, 0x08, 0x03)); // accel + gyro
            vTaskDelay(pdMS_TO_TICKS(200));
            sensors->imu_ok = true;
            ESP_LOGI(TAG, "QMI8658B online, id=0x%02x rev=0x%02x", identity[0], identity[1]);
        }
    }

    if (i2c_master_probe(bus, MQC5883P_ADDRESS, 50) == ESP_OK &&
        add_device(bus, MQC5883P_ADDRESS, &sensors->mag) == ESP_OK) {
        uint8_t identity = 0;
        if (reg_read(sensors->mag, 0x00, &identity, 1) == ESP_OK && identity == 0x80) {
            ESP_ERROR_CHECK_WITHOUT_ABORT(reg_write(sensors->mag, 0x0d, 0x40));
            vTaskDelay(pdMS_TO_TICKS(10));
            ESP_ERROR_CHECK_WITHOUT_ABORT(reg_write(sensors->mag, 0x29, 0x06));
            vTaskDelay(pdMS_TO_TICKS(10));
            ESP_ERROR_CHECK_WITHOUT_ABORT(reg_write(sensors->mag, 0x0a, 0xcf));
            vTaskDelay(pdMS_TO_TICKS(10));
            ESP_ERROR_CHECK_WITHOUT_ABORT(reg_write(sensors->mag, 0x0b, 0x08));
            vTaskDelay(pdMS_TO_TICKS(20));
            sensors->mag_ok = true;
            ESP_LOGI(TAG, "MQC5883P online, id=0x%02x", identity);
        }
    }

    if (!sensors->imu_ok) {
        ESP_LOGW(TAG, "QMI8658B not detected");
    }
    if (!sensors->mag_ok) {
        ESP_LOGW(TAG, "MQC5883P not detected at 0x2c");
    }
    return sensors->imu_ok ? ESP_OK : ESP_ERR_NOT_FOUND;
}

void whisplay_sensors_read(whisplay_sensors_t *sensors, whisplay_sensor_sample_t *sample)
{
    memset(sample, 0, sizeof(*sample));
    if (sensors->imu_ok) {
        uint8_t raw[14] = {0};
        if (reg_read(sensors->imu, 0x33, raw, sizeof(raw)) == ESP_OK) {
            sample->temperature_c = i16le(raw) / 256.0f;
            sample->ax_g = i16le(&raw[2]) / 4096.0f;
            sample->ay_g = i16le(&raw[4]) / 4096.0f;
            sample->az_g = i16le(&raw[6]) / 4096.0f;
            sample->gx_dps = i16le(&raw[8]) / 64.0f;
            sample->gy_dps = -i16le(&raw[10]) / 64.0f;
            sample->gz_dps = -i16le(&raw[12]) / 64.0f;
        }
    }

    if (sensors->mag_ok) {
        uint8_t status = 0;
        uint8_t raw[6] = {0};
        if (reg_read(sensors->mag, 0x09, &status, 1) == ESP_OK &&
            reg_read(sensors->mag, 0x01, raw, sizeof(raw)) == ESP_OK) {
            const int16_t x = i16le(&raw[0]);
            const int16_t y = i16le(&raw[2]);
            sample->mag_x = y;
            sample->mag_y = -x;
            sample->mag_z = i16le(&raw[4]);
            sample->mag_status = status;
            sample->heading_deg = atan2f(sample->mag_y, sample->mag_x) * 57.2957795f;
            if (sample->heading_deg < 0) {
                sample->heading_deg += 360.0f;
            }
        }
    }
}

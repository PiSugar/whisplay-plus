#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "driver/i2c_master.h"
#include "esp_err.h"

typedef struct {
    i2c_master_dev_handle_t imu;
    i2c_master_dev_handle_t mag;
    bool imu_ok;
    bool mag_ok;
    uint8_t imu_revision;
} whisplay_sensors_t;

typedef struct {
    float ax_g, ay_g, az_g;
    float gx_dps, gy_dps, gz_dps;
    float temperature_c;
    int16_t mag_x, mag_y, mag_z;
    float heading_deg;
    uint8_t mag_status;
} whisplay_sensor_sample_t;

esp_err_t whisplay_sensors_init(i2c_master_bus_handle_t bus, whisplay_sensors_t *sensors);
void whisplay_sensors_read(whisplay_sensors_t *sensors, whisplay_sensor_sample_t *sample);

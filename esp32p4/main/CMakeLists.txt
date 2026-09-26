idf_component_register(
    SRCS "main.c" "demo_ui.c" "whisplay_sensors.c" "whisplay_audio.c" "whisplay_nfc.c"
    INCLUDE_DIRS "."
    REQUIRES esp_driver_gpio esp_driver_i2c esp_driver_i2s esp_hw_support esp_lcd espressif__esp_codec_dev
)

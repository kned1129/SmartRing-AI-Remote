idf_component_register(SRCS "main.c" "diagnostics.c" "recording_fsm_test.c" "remote_ui.c" "smartring_cjk_16.c" INCLUDE_DIRS "." REQUIRES esp_wifi esp_netif nvs_flash json lwip mbedtls esp_timer)

#pragma once
#include <stdint.h>
#include "lvgl.h"
void diagnostics_init(void);
int diagnostics_phase(void);
void diagnostics_page(uint32_t us);
void diagnostics_audio(uint32_t us, int fault);
void diagnostics_wifi(uint32_t us);
bool diagnostics_touch(lv_point_t *point);

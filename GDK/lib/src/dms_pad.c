#include <stdint.h>
#include "dms1_hw.h"
#include "dms_pad.h"

static uint8_t g_previous;
static uint8_t raw_pad(void) { return *(volatile uint8_t*)DMS_PAD_BASE; }
void dms_pad_runtime_init(void) { g_previous = raw_pad(); }
void dms_pad_frame_boundary(void) { g_previous = raw_pad(); }
uint8_t PAD_read(void) { return raw_pad(); }
uint8_t PAD_pressed(uint8_t mask) {
    uint8_t now = raw_pad();
    return (uint8_t)((now & mask) & (uint8_t)~g_previous);
}
uint8_t PAD_released(uint8_t mask) {
    uint8_t now = raw_pad();
    return (uint8_t)((g_previous & mask) & (uint8_t)~now);
}

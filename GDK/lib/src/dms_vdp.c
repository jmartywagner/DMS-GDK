#include <stdint.h>
#include "dms1_hw.h"
#include "dms_vdp.h"

static volatile uint8_t * const vdp = (volatile uint8_t*)DMS_VDP_BASE;
static volatile uint16_t * const cram = (volatile uint16_t*)DMS_CRAM_BASE;
static uint8_t g_vdp_mode = DMS_MODE_STANDARD;

void VDP_setMode(uint8_t mode) {
    while ((vdp[0] & 1u) == 0u) { }
    vdp[2] = mode;
    g_vdp_mode = mode;
}

uint8_t VDP_getMode(void) { return g_vdp_mode; }

void VDP_setPalette(uint8_t palette, const uint16_t* rgb333_words) {
    uint16_t base = (uint16_t)((palette & 7u) * 16u);
    uint16_t i;
    for (i = 0; i < 16u; ++i) cram[base + i] = (uint16_t)(rgb333_words[i] & 0x01FFu);
}

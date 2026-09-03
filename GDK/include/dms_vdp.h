#ifndef DMS_VDP_H
#define DMS_VDP_H
#include <stdint.h>
#define DMS_MODE_STANDARD   0
#define DMS_MODE_HIGH_COLOR 1
#define DMS_MODE_SCROLL     2
#define DMS_MODE_SPRITE     3
#define DMS_MODE_LOW_RES    4
void VDP_setMode(uint8_t mode);
uint8_t VDP_getMode(void);
void VDP_setPalette(uint8_t palette, const uint16_t* rgb333_words);
#endif

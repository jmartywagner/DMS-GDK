#ifndef DMS_SPRITE_H
#define DMS_SPRITE_H
#include <stdint.h>
typedef uint16_t DmsSprite;
DmsSprite SPR_create(uint16_t dres_resource_id, int16_t x, int16_t y);
DmsSprite SPR_createNoPalette(uint16_t dres_resource_id, int16_t x, int16_t y);
void SPR_destroy(DmsSprite sprite);
void SPR_setPosition(DmsSprite sprite, int16_t x, int16_t y);
void SPR_setAnimation(DmsSprite sprite, uint16_t animation_id);
void SPR_setFrame(DmsSprite sprite, uint16_t frame_id);
void SPR_setVisible(DmsSprite sprite, uint8_t visible);
void SPR_setPriority(DmsSprite sprite, uint8_t in_front);
void SPR_setScreenSpace(DmsSprite sprite, uint8_t screen_space);
void SPR_setFlipX(DmsSprite sprite, uint8_t flipped);
void SPR_setPalette(DmsSprite sprite, uint8_t palette_id);
/* BUILD 04 : primitives bas niveau contrôlées pour HUD fixe / restauration VRAM. */
uint16_t SPR_reservePatterns(uint16_t pattern_count);
void SPR_uploadPatterns(uint16_t base_tile, const uint8_t* tile_data, uint16_t pattern_count);
void SPR_uploadPalette(uint8_t palette_id, const uint16_t* rgb333_words);
DmsSprite SPR_createRaw16(uint16_t tile_base, int16_t x, int16_t y, uint8_t palette_id, uint8_t priority);
void SPR_reloadAllResources(void);
void SPR_reloadAllResourcesNoPalette(void);
void SPR_reloadAllPalettes(void);
uint16_t SPR_hwUsedCount(void);
uint16_t SPR_hwModeLimit(void);
uint16_t SPR_hwAvailableCount(void);
#endif

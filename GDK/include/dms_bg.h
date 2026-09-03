#ifndef DMS_BG_H
#define DMS_BG_H
#include <stdint.h>
void BG_loadMap(uint16_t map_resource_id);
/* Variante de transition : charge motifs/rings sans toucher à la CRAM, puis
   BG_reloadMapPalettes() applique rapidement les palettes cibles à la fin. */
void BG_loadMapKeepBlack(uint16_t map_resource_id);
void BG_reloadMapPalettes(void);
void BG_setVideoMode(uint8_t mode);
uint8_t BG_videoMode(void);
void BG_setScroll(int16_t x, int16_t y);
void BG_setScrollA(int16_t x, int16_t y);
void BG_setScrollB(int16_t x, int16_t y);
/* Téléportation de ring: recharge directement la fenêtre cible sans parcourir
   toutes les colonnes intermédiaires. Destiné aux transitions sous écran noir. */
void BG_jumpScrollA(int16_t x, int16_t y);
void BG_jumpScrollB(int16_t x, int16_t y);
/* Remplace à chaud les 32 octets 4bpp d'un motif du tileset BG A chargé.
   tile_id est l'index local dans le DIMG/tileset courant. */
void BG_replaceTilePattern(uint16_t tile_id, const uint8_t *pattern32);
/* Ponts internes utilisés par le runtime sprite. */
int16_t dms_bg_scroll_x(void);
int16_t dms_bg_scroll_y(void);
uint16_t dms_bg_pattern_limit(void);
uint16_t dms_bg_pattern_floor(void);
#endif

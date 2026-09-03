#ifndef DMS_FX_H
#define DMS_FX_H

#include <stdint.h>

/* DMS-1 reusable video FX.
   The game remains owner of camera/base scroll. FX_update() composites additive
   offsets, palette transforms and (MODE 2 only) hardware line-scroll tables. */
typedef enum {
    DMS_FX_NONE = 0,
    DMS_FX_SHAKE,
    DMS_FX_KICK,
    DMS_FX_FLASH,
    DMS_FX_FADE_OUT,
    DMS_FX_FADE_IN,
    DMS_FX_PULSE,
    DMS_FX_COLOR_CYCLE,
    DMS_FX_WATER_WAVE,
    DMS_FX_RIPPLE,
    DMS_FX_HEAT_HAZE,
    DMS_FX_SHEAR_WOBBLE,
    DMS_FX_RASTER_SPLIT,
    DMS_FX_SCAN_SWEEP,
    DMS_FX_SPEED_BANDS,
    DMS_FX_BG_PARALLAX_OSC,

    /* FX LIBRARY - VOLUME 2 */
    DMS_FX_PALETTE_INVERT,
    DMS_FX_PALETTE_TINT,
    DMS_FX_PALETTE_DESATURATE,
    DMS_FX_PALETTE_STROBE,
    DMS_FX_HIT_FREEZE_VISUAL,
    DMS_FX_EARTHQUAKE_RASTER,
    DMS_FX_PERSPECTIVE_WARP,
    DMS_FX_UNDERWATER_DRIFT,
    DMS_FX_PARALLAX_KICK,
    DMS_FX_BG_DEPTH_SWAY,
    DMS_FX_COUNT
} DmsFxId;

typedef struct {
    uint8_t intensity;      /* 0..15 */
    uint8_t secondary;      /* 0..15 */
    uint16_t duration;      /* frames, 0 = library default */
    uint8_t palette_mask;   /* bit P0..P7; ignored by non-palette FX */
    uint16_t color;         /* RGB333 */
    uint8_t palette;        /* COLOR_CYCLE palette */
    uint8_t first_color;    /* COLOR_CYCLE first index */
    uint8_t color_count;    /* COLOR_CYCLE range length */
    uint8_t attack;         /* optional envelope attack frames */
    uint8_t hold;           /* optional envelope hold frames */
    uint8_t release;        /* optional envelope release frames */
} DmsFxParams;

typedef struct {
    uint8_t attack;
    uint8_t hold;
    uint8_t release;
    uint8_t peak;           /* 0..15 */
} DmsFxEnvelope;

#define DMS_FX_STACK_MAX 4u
#define DMS_FX_RASTER_ZONES_MAX 8u

typedef struct {
    uint8_t y0;             /* inclusive, 0..223 */
    uint8_t y1;             /* inclusive, 0..223 */
    int8_t offset_a;        /* constant additive X offset */
    int8_t offset_b;
    uint8_t wave_amp;       /* optional sine modulation */
    uint8_t wave_speed;     /* 0 = static */
} DmsFxRasterZone;

void FX_init(uint8_t video_mode);
void FX_setMode(uint8_t video_mode);
/* Change de mode après un fade-out terminé sans restaurer la palette source.
   La palette reste noire et FX_fadeIn() réutilise la cible déjà capturée. */
void FX_setModeKeepBlack(uint8_t video_mode);
/* Capture the current target palettes, force the selected palettes to black,
   and keep them black until FX_fadeIn(). This is intended for map/mode loads
   performed between a completed fade-out and a later fade-in. */
void FX_holdBlack(uint8_t palette_mask);
uint8_t FX_getMode(void);
uint8_t FX_isCompatible(DmsFxId id, uint8_t video_mode);
const char* FX_name(DmsFxId id);

/* Classic single-effect API. Starting one effect stops the previous stack. */
uint8_t FX_start(DmsFxId id, const DmsFxParams* params);
void FX_stop(void);
void FX_reset(void);
uint8_t FX_active(void);
DmsFxId FX_current(void);
uint16_t FX_framesLeft(void);
void FX_update(int16_t base_ax, int16_t base_ay,
               int16_t base_bx, int16_t base_by);

/* Volume 2 compositing API. Up to four compatible effects are mixed in one
   frame. Line-scroll contributors are summed then clamped before the one real
   MODE 2 table write. Palette contributors always derive from a saved CRAM
   baseline, so effects do not recursively damage the game's colors. */
uint8_t FX_stackAdd(DmsFxId id, const DmsFxParams* params);
void FX_stackClear(void);
uint8_t FX_stackCount(void);
uint8_t FX_stackContains(DmsFxId id);

/* Generic ADSR-like visual envelope (linear, integer, non-blocking). */
uint8_t FX_envelopeValue(const DmsFxEnvelope* env, uint16_t age);

/* Persistent MODE 2 raster composer. Zones are additive to camera and stack. */
void FX_rasterComposerClear(void);
uint8_t FX_rasterComposerSet(uint8_t index, const DmsFxRasterZone* zone);
void FX_rasterComposerEnable(uint8_t enable);
uint8_t FX_rasterComposerEnabled(void);

/* HIT_FREEZE_VISUAL never freezes game logic itself. The game may query this
   hint and decide whether its simulation should pause for the indicated frame. */
uint8_t FX_hitFreezeRequested(void);

/* Convenience wrappers. */
uint8_t FX_shake(uint8_t amplitude, uint16_t duration, uint8_t attenuation);
uint8_t FX_kick(int8_t dir_x, int8_t dir_y, uint8_t amplitude, uint16_t duration);
uint8_t FX_flash(uint16_t rgb333, uint8_t palette_mask, uint16_t duration);
uint8_t FX_fadeOut(uint8_t palette_mask, uint16_t duration);
uint8_t FX_fadeIn(uint8_t palette_mask, uint16_t duration);

#endif

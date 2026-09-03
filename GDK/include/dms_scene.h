#ifndef DMS_SCENE_H
#define DMS_SCENE_H

#include <stdint.h>

#define DMS_SCENE_OBJECT_MAX 128u
#define DMS_SCENE_INVALID 0xFFFFu

#define DMS_SCENE_KIND_SPRITE      1u
#define DMS_SCENE_KIND_ACTOR       2u
#define DMS_SCENE_KIND_ATMOSPHERE  3u
#define DMS_SCENE_KIND_TEXT        4u
#define DMS_SCENE_KIND_UI          5u
#define DMS_SCENE_KIND_TRANSITION  6u

#define DMS_SCENE_LAYER_BG_B          0u
#define DMS_SCENE_LAYER_BG_A_BEHIND   1u
#define DMS_SCENE_LAYER_ACTORS        2u
#define DMS_SCENE_LAYER_BG_A_FRONT    3u
#define DMS_SCENE_LAYER_ATMOSPHERE    4u
#define DMS_SCENE_LAYER_UI            5u
#define DMS_SCENE_LAYER_TRANSITION    6u
#define DMS_SCENE_PALETTE_STATIC      0u
#define DMS_SCENE_PALETTE_CYCLE       1u
#define DMS_SCENE_OP_SHOW              1u
#define DMS_SCENE_OP_HIDE              2u
#define DMS_SCENE_OP_TYPEWRITER        3u
#define DMS_SCENE_OP_SLIDE_IN          4u
#define DMS_SCENE_OP_FX_START          5u
#define DMS_SCENE_OP_MUSIC_PLAY        6u
#define DMS_SCENE_OP_MUSIC_STOP        7u
#define DMS_SCENE_OP_SFX_PLAY          8u
#define DMS_SCENE_OP_MENU_ENABLE       9u
#define DMS_SCENE_OP_WAIT_INPUT       10u
#define DMS_SCENE_OP_END              11u
#define DMS_SCENE_OP_CAMERA_SET       12u
#define DMS_SCENE_OP_CAMERA_SPEED     13u
#define DMS_SCENE_OP_SCROLL_SET       14u
#define DMS_SCENE_OP_VIDEO_MODE       15u
#define DMS_SCENE_OP_TRIGGER          16u
#define DMS_SCENE_OP_SPAWN_FORMATION 17u
#define DMS_SCENE_OP_CHECKPOINT       18u
#define DMS_SCENE_OP_FLOW_EMIT        19u

#define DMS_SCENE_OPTION_NONE        0u
#define DMS_SCENE_OPTION_LIVES       1u
#define DMS_SCENE_OPTION_MUSIC_TEST  2u
#define DMS_SCENE_OPTION_SFX_TEST    3u

typedef struct {
    uint16_t resource_id;
    const char *text;
    int16_t x, y;
    int16_t velocity_x_q8, velocity_y_q8;
    int16_t parallax_x_q8, parallax_y_q8;
    int16_t spawn_x, spawn_y;
    int16_t despawn_left, despawn_right, despawn_top, despawn_bottom;
    uint16_t animation_id;
    uint16_t animation_cadence;
    uint16_t start_frame, end_frame;
    uint16_t start_trigger, end_trigger;
    uint8_t kind, layer, priority, palette;
    uint8_t palette_animation, palette_span;
    uint16_t palette_cadence;
    uint8_t visible, loop, screen_space, direction;
    uint16_t action_event;
    int16_t option_min, option_max, option_step, option_value;
    uint8_t selected_palette, option_type;
} DmsSceneObjectResourceDesc;

typedef struct {
    uint16_t frame;
    uint8_t op, target;
    int16_t a, b, c, d;
    uint16_t ref;
} DmsSceneRuntimeEventDesc;

typedef struct {
    uint16_t resource_id;
    const char *name;
    const DmsSceneObjectResourceDesc *objects;
    uint16_t object_count;
    const DmsSceneRuntimeEventDesc *events;
    uint16_t event_count;
    uint16_t map_resource_id;
    int16_t scroll_a_x, scroll_a_y, scroll_b_x, scroll_b_y;
    int16_t parallax_a_x_q8, parallax_a_y_q8;
    int16_t parallax_b_x_q8, parallax_b_y_q8;
    int16_t camera_x, camera_y, camera_speed_x_q8, camera_speed_y_q8;
    uint16_t menu_move_sfx, menu_validate_sfx;
    uint8_t video_mode;
    uint8_t flags;
} DmsSceneResourceDesc;

/* API des scènes .dscene V2 compilées par dmsres. */
uint8_t SCENE_start(uint16_t scene_resource_id);
void SCENE_setCamera(int16_t x, int16_t y);
int16_t SCENE_cameraX(void);
int16_t SCENE_cameraY(void);
void SCENE_setCameraSpeed(int16_t vx_q8, int16_t vy_q8);
void SCENE_trigger(uint16_t trigger_id);
uint16_t SCENE_current(void);
uint8_t SCENE_objectActive(uint16_t object_index);

/* Structures V1 conservées : les exports C/H/BIN existants restent valides. */
typedef struct {
    const uint8_t *tiles;
    uint16_t tile_count;
    const uint16_t *palettes;
    const uint8_t *palette_ids;
    uint8_t palette_count;
    const uint16_t *map_a;
    const uint16_t *map_b;
    uint8_t map_width, map_height;
} DmsSceneVisual;

typedef struct {
    const char *text;
    int16_t x, y;
    uint16_t action;
    uint8_t kind, palette, selected_palette, visible;
} DmsSceneObjectDesc;

typedef struct {
    uint16_t frame;
    uint8_t op, target;
    int16_t a, b, c, d;
    uint16_t ref;
} DmsSceneEvent;

typedef struct {
    uint8_t video_mode;
    int16_t scroll_a_x, scroll_a_y, scroll_b_x, scroll_b_y;
    const DmsSceneVisual *visual;
    const uint8_t *font_tiles;
    const uint16_t *font_palettes;
    const uint8_t *font_palette_ids;
    uint8_t font_palette_count;
    const DmsSceneObjectDesc *objects;
    uint8_t object_count;
    const DmsSceneEvent *events;
    uint16_t event_count;
    uint16_t menu_move_sfx, menu_validate_sfx;
} DmsSceneDef;

void SCENE_play(const DmsSceneDef *scene);
void SCENE_update(void);
uint8_t SCENE_isActive(void);
void SCENE_stop(void);
uint16_t SCENE_frame(void);
uint16_t SCENE_result(void);
uint8_t SCENE_menuIndex(void);

#endif

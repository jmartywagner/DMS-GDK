#ifndef DMS_GAME_SETTINGS_H
#define DMS_GAME_SETTINGS_H

#include <stdint.h>

/* L'ordre est identique à GDK/tools/dms_game_settings.py. Les vitesses et
 * ratios utilisent le format signé 8.8 (256 = 1,0). */
typedef struct {
    int16_t player_speed_q8;
    int16_t crouch_speed_q8;
    int16_t acceleration_q8;
    int16_t instant_stop;
    int16_t gravity_q8;
    int16_t jump_impulse_q8;
    int16_t double_jump_q8;
    int16_t short_jump_q8;
    int16_t long_jump_frames;
    int16_t max_fall_q8;
    int16_t camera_offset_x;
    int16_t camera_offset_y;
    int16_t camera_deadzone_x;
    int16_t camera_deadzone_y;
    int16_t camera_scroll_q8;
    int16_t camera_limit_left;
    int16_t camera_limit_top;
    int16_t camera_limit_right;
    int16_t camera_limit_bottom;
    int16_t parallax_a_x_q8;
    int16_t parallax_a_y_q8;
    int16_t parallax_b_x_q8;
    int16_t parallax_b_y_q8;
    int16_t difficulty;
    int16_t starting_lives;
    int16_t continues_count;
    int16_t title_frames;
    int16_t transition_frames;
    int16_t game_over_frames;
    int16_t cloud0_enabled;
    int16_t cloud0_start_x;
    int16_t cloud0_y;
    int16_t cloud0_speed_q8;
    int16_t cloud0_cadence;
    int16_t cloud1_enabled;
    int16_t cloud1_start_x;
    int16_t cloud1_y;
    int16_t cloud1_speed_q8;
    int16_t cloud1_cadence;
    int16_t ambience_despawn_x;
    int16_t foreground_enabled;
} DmsGameSettings;

extern const DmsGameSettings dms_game_settings;

#endif

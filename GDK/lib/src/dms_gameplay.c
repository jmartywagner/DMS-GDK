#include <stdint.h>
#include "dms_game_settings.h"
#include "dms_gameplay.h"

typedef struct {
    uint16_t starting_lives;
    uint16_t lives;
    uint8_t checkpoint_valid;
    int16_t checkpoint_x, checkpoint_y;
    int16_t checkpoint_camera_x, checkpoint_camera_y;
} DmsGameplayState;

static DmsGameplayState g_gameplay;

void dms_gameplay_runtime_init(void){
    uint16_t lives=(uint16_t)(dms_game_settings.starting_lives>0?dms_game_settings.starting_lives:1);
    g_gameplay.starting_lives=lives;
    g_gameplay.lives=lives;
    g_gameplay.checkpoint_valid=0u;
}
void GAMEPLAY_reset(void){g_gameplay.lives=g_gameplay.starting_lives;g_gameplay.checkpoint_valid=0u;}
void GAMEPLAY_setStartingLives(uint16_t lives){if(!lives)lives=1u;g_gameplay.starting_lives=lives;g_gameplay.lives=lives;}
uint16_t GAMEPLAY_lives(void){return g_gameplay.lives;}
uint16_t GAMEPLAY_startingLives(void){return g_gameplay.starting_lives;}
void GAMEPLAY_addLife(uint16_t amount){uint32_t n=(uint32_t)g_gameplay.lives+amount;g_gameplay.lives=(uint16_t)(n>65535u?65535u:n);}
uint8_t GAMEPLAY_loseLife(void){if(g_gameplay.lives) --g_gameplay.lives;return g_gameplay.lives?1u:0u;}
void GAMEPLAY_setCheckpoint(int16_t x,int16_t y,int16_t camera_x,int16_t camera_y){g_gameplay.checkpoint_x=x;g_gameplay.checkpoint_y=y;g_gameplay.checkpoint_camera_x=camera_x;g_gameplay.checkpoint_camera_y=camera_y;g_gameplay.checkpoint_valid=1u;}
void GAMEPLAY_clearCheckpoint(void){g_gameplay.checkpoint_valid=0u;}
uint8_t GAMEPLAY_hasCheckpoint(void){return g_gameplay.checkpoint_valid;}
int16_t GAMEPLAY_checkpointX(void){return g_gameplay.checkpoint_x;}
int16_t GAMEPLAY_checkpointY(void){return g_gameplay.checkpoint_y;}
int16_t GAMEPLAY_checkpointCameraX(void){return g_gameplay.checkpoint_camera_x;}
int16_t GAMEPLAY_checkpointCameraY(void){return g_gameplay.checkpoint_camera_y;}

#ifndef DMS_ACTOR_H
#define DMS_ACTOR_H
#include <stdint.h>
typedef uint16_t DmsActor;
DmsActor ACTOR_spawn(uint16_t dactor_resource_id, int16_t x, int16_t y);
void ACTOR_destroy(DmsActor actor);
void ACTOR_update(void);
void ACTOR_setVisible(DmsActor actor, uint8_t visible);
void ACTOR_setPriority(DmsActor actor, uint8_t in_front);
void ACTOR_setPalette(DmsActor actor, uint8_t palette_id);
int16_t ACTOR_x(DmsActor actor);
int16_t ACTOR_y(DmsActor actor);
uint16_t ACTOR_state(DmsActor actor);
void ACTOR_damage(DmsActor actor, uint16_t damage);
void ACTOR_heal(DmsActor actor, uint16_t amount);
uint16_t ACTOR_hp(DmsActor actor);
uint8_t ACTOR_isAlive(DmsActor actor);
void ACTOR_setVelocity(DmsActor actor, int16_t vx_q8, int16_t vy_q8);
void ACTOR_setPosition(DmsActor actor, int16_t x, int16_t y);
void ACTOR_destroyAll(void);
uint16_t ACTOR_count(void);
DmsActor ACTOR_player(void);
#endif

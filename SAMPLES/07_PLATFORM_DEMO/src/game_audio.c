#include <stdint.h>
#include "dms1_hw.h"
#include "game_audio.h"
#include "sfx_generated.h"
typedef struct{uint16_t start_page,end_page,rate_hz;uint8_t codec,level,hold;}Def;
static volatile uint8_t * const mail=(volatile uint8_t*)DMS_MAILBOX_BASE;
static uint8_t guard,prio;
static const Def defs[GAME_SFX_COUNT]={
 {GAME_SFX_JUMP_START_PAGE,GAME_SFX_JUMP_END_PAGE,GAME_SFX_JUMP_RATE_HZ,GAME_SFX_JUMP_CODEC,8u,3u},
 {GAME_SFX_PICKUP_START_PAGE,GAME_SFX_PICKUP_END_PAGE,GAME_SFX_PICKUP_RATE_HZ,GAME_SFX_PICKUP_CODEC,7u,4u},
 {GAME_SFX_SPRING_START_PAGE,GAME_SFX_SPRING_END_PAGE,GAME_SFX_SPRING_RATE_HZ,GAME_SFX_SPRING_CODEC,6u,6u},
 {GAME_SFX_HIT_START_PAGE,GAME_SFX_HIT_END_PAGE,GAME_SFX_HIT_RATE_HZ,GAME_SFX_HIT_CODEC,3u,7u},
 {GAME_SFX_BOOST_START_PAGE,GAME_SFX_BOOST_END_PAGE,GAME_SFX_BOOST_RATE_HZ,GAME_SFX_BOOST_CODEC,8u,5u},
 {GAME_SFX_CHECKPOINT_START_PAGE,GAME_SFX_CHECKPOINT_END_PAGE,GAME_SFX_CHECKPOINT_RATE_HZ,GAME_SFX_CHECKPOINT_CODEC,6u,10u},
 {GAME_SFX_DEATH_START_PAGE,GAME_SFX_DEATH_END_PAGE,GAME_SFX_DEATH_RATE_HZ,GAME_SFX_DEATH_CODEC,4u,12u},
};
static uint8_t pan(int16_t x){if(x<106)return 0x80u;if(x>213)return 0x40u;return 0xC0u;}
static uint16_t dn(uint16_t r){uint32_t v=((uint32_t)r*65536u+27777u)/55556u;if(!v)v=1u;if(v>65535u)v=65535u;return(uint16_t)v;}
void GAME_AUDIO_init(void){guard=0u;prio=0u;}
void GAME_AUDIO_tick(void){if(guard&&!--guard)prio=0u;}
void GAME_AUDIO_play(uint8_t id,uint8_t p,int16_t x){const Def*d;uint16_t delta;if(id>=GAME_SFX_COUNT)return;if(guard&&p<prio)return;if(mail[0]!=0u)return;d=&defs[id];delta=d->codec==2u?dn(d->rate_hz):0u;mail[3]=d->codec;mail[4]=(uint8_t)d->start_page;mail[5]=(uint8_t)(d->start_page>>8);mail[6]=(uint8_t)d->end_page;mail[7]=(uint8_t)(d->end_page>>8);mail[8]=d->level;mail[9]=pan(x);mail[10]=(uint8_t)delta;mail[11]=(uint8_t)(delta>>8);mail[12]=0u;mail[0]=3u;guard=d->hold;prio=p;}

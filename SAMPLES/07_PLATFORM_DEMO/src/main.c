#include <stdint.h>
#include "dms1.h"
#include "dms1_hw.h"
#include "resources.h"
#include "platform_data.h"
#include "platform_video.h"
#include "game_audio.h"

#define SCREEN_W 320
#define LOWRES_W 256
#define SCREEN_H 224
#define OFFSCREEN 511
#define FP_SHIFT 8
#define FP_ONE 256
#define PLAYER_W 16
#define PLAYER_H 16
#define ACCEL 34
#define FRICTION 26
#define MAX_RUN (7*FP_ONE)
#define BOOST_RUN (9*FP_ONE)
#define GRAVITY 64
#define JUMP_V (-1472)
#define SPRING_V (-2304)
#define MAX_FALL (8*FP_ONE)
#define COLLECTIBLE_MAX 24
#define ENEMY_MAX 10
#define SPRING_MAX 3
#define BOOSTER_MAX 2
#define PLATFORM_MAX 4
#define PLAYER_FRAME_SPRITES 5
#define HUD_DIGIT_POS 2
#define PERF_LIGHTS 4
#define MODE_ICONS 5
#define SPRITE_STORM 45
#define MODE0_END 800
#define MODE2_END 1500
#define MODE1_END 2300
#define MODE3_END 3300
#define FADE_MAX 8u
#define BASE_SPRITE_SLOTS (PLAYER_FRAME_SPRITES+1+MODE_ICONS+2+PLATFORM_MAX+SPRING_MAX+BOOSTER_MAX+ENEMY_MAX+COLLECTIBLE_MAX+PERF_LIGHTS+3+(HUD_DIGIT_POS*10))
#define TOTAL_SPRITE_SLOTS (BASE_SPRITE_SLOTS+SPRITE_STORM)
#if BASE_SPRITE_SLOTS != 83
#error "07_PLATFORM_DEMO V0.3 base allocation must stay at 83 slots"
#endif
#if TOTAL_SPRITE_SLOTS != 128
#error "07_PLATFORM_DEMO V0.3 must allocate exactly 128 slots for Mode 3"
#endif

typedef struct { int32_t x,y,vx,vy; uint8_t grounded,ground_platform,dead,death_timer,hurt_timer,debug_invincible; } Player;
typedef struct { DmsSprite spr; int16_t x,y; uint8_t active; } Collectible;
typedef struct { DmsSprite spr; int16_t base_x,x,y,range; int8_t dir; uint8_t active,kind; } Enemy;
typedef struct { DmsSprite spr; int16_t x,y; } Spring;
typedef struct { DmsSprite spr; int16_t x,y; int8_t direction; } Booster;
typedef struct { DmsSprite spr; int16_t base_x,base_y,x,y,range; int8_t dir; uint8_t axis; } MovingPlatform;

static Player player;
static DmsSprite player_frames[PLAYER_FRAME_SPRITES];
static DmsSprite shield_sprite;
static DmsSprite mode_icons[MODE_ICONS];
static DmsSprite sprite_storm[SPRITE_STORM];
static uint16_t storm_x[SPRITE_STORM];
static uint16_t storm_y[SPRITE_STORM];
static uint8_t storm_dx[SPRITE_STORM];
static Collectible collectibles[COLLECTIBLE_MAX];
static Enemy enemies[ENEMY_MAX];
static Spring springs[SPRING_MAX];
static Booster boosters[BOOSTER_MAX];
static MovingPlatform moving_platforms[PLATFORM_MAX];
static DmsSprite checkpoint_off,checkpoint_on;
static DmsSprite life_icons[3];
static DmsSprite hud_digits[HUD_DIGIT_POS][10];
static DmsSprite perf_lights[PERF_LIGHTS];
static uint8_t collectible_count,enemy_count,spring_count,booster_count,platform_count;
static uint8_t lives,collected,checkpoint_active,music_on,finish_timer;
static uint8_t current_mode,target_mode,transition_state,fade_level,rebuild_pending;
static uint16_t checkpoint_x,checkpoint_y,camera_x,frame_counter,last_stream_count,stream_delta;
static int16_t checkpoint_object_x,checkpoint_object_y;

static int16_t px(void){return (int16_t)(player.x>>FP_SHIFT);} static int16_t py(void){return (int16_t)(player.y>>FP_SHIFT);}
static int16_t iabs16(int16_t v){return v<0?(int16_t)-v:v;}
static void hide(DmsSprite s){SPR_setPosition(s,OFFSCREEN,OFFSCREEN);}
static uint8_t overlap(int16_t ax,int16_t ay,int16_t aw,int16_t ah,int16_t bx,int16_t by,int16_t bw,int16_t bh){return(uint8_t)(!(ax+aw<=bx||bx+bw<=ax||ay+ah<=by||by+bh<=ay));}
static uint8_t mode_for_world_x(int16_t x){if(x<MODE0_END)return DMS_MODE_STANDARD;if(x<MODE2_END)return DMS_MODE_SCROLL;if(x<MODE1_END)return DMS_MODE_HIGH_COLOR;if(x<MODE3_END)return DMS_MODE_SPRITE;return DMS_MODE_LOW_RES;}
static int16_t active_screen_width(void){return current_mode==DMS_MODE_LOW_RES?LOWRES_W:SCREEN_W;}
static int16_t screen_x(int16_t world_x){return (int16_t)(world_x-(int16_t)camera_x);}
static void place_world(DmsSprite s,int16_t wx,int16_t wy){int16_t sx=screen_x(wx),sw=active_screen_width();if(sx<-24||sx>sw+8||wy<-24||wy>SCREEN_H+8)hide(s);else SPR_setPosition(s,sx,wy);}

static int16_t slope_y(const PlatformZoneDef*z,int16_t x){
    int32_t dx=(int32_t)z->bx-z->ax;int32_t dy=(int32_t)z->by-z->ay;
    if(dx==0)return z->ay;
    return(int16_t)(z->ay+(int32_t)(x-z->ax)*dy/dx);
}

static uint8_t x_intersects_zone(int16_t x,int16_t w,const PlatformZoneDef*z){return(uint8_t)(x+w>z->x0&&x<z->x1);}

static void collide_horizontal(int16_t oldx,int16_t newx,int16_t y){
    uint16_t i;(void)oldx;
    for(i=0;i<PLATFORM_ZONE_COUNT;++i){
        const PlatformZoneDef*z=&platform_zones[i];
        if(z->type!=PCOLL_SOLID||z->shape!=PSHAPE_RECT)continue;
        if(y+PLAYER_H<=z->y0||y>=z->y1)continue;
        if(newx+PLAYER_W<=z->x0||newx>=z->x1)continue;
        if(player.vx>0){newx=(int16_t)(z->x0-PLAYER_W);}else if(player.vx<0){newx=z->x1;}
        player.x=(int32_t)newx<<FP_SHIFT;player.vx=0;return;
    }
    player.x=(int32_t)newx<<FP_SHIFT;
}

static uint8_t moving_floor(int16_t x,int16_t old_bottom,int16_t new_bottom,int16_t*floor_y,uint8_t*which){
    uint8_t i,hit=0;int16_t best=32767;
    for(i=0;i<platform_count;++i){MovingPlatform*m=&moving_platforms[i];int16_t top=m->y;if(x+PLAYER_W<=m->x||x>=m->x+16)continue;if(old_bottom<=top+4&&new_bottom>=top&&top<best){best=top;*which=i;hit=1;}}
    if(hit) *floor_y=best;
    return hit;
}

static void collide_vertical(int16_t x,int16_t oldy,int16_t newy){
    uint16_t i;int16_t old_bottom=(int16_t)(oldy+PLAYER_H),new_bottom=(int16_t)(newy+PLAYER_H);int16_t best=32767;uint8_t hit=0;uint8_t gp=255;
    if(player.vy>=0){
        for(i=0;i<PLATFORM_ZONE_COUNT;++i){
            const PlatformZoneDef*z=&platform_zones[i];int16_t floor;
            if(z->type!=PCOLL_SOLID&&z->type!=PCOLL_ONEWAY)continue;
            if(z->shape==PSHAPE_RECT){
                if(!x_intersects_zone(x,PLAYER_W,z)) continue;
                floor=z->y0;
                if(old_bottom<=floor+4&&new_bottom>=floor&&floor<best){best=floor;hit=1;gp=255;}
            }else if(z->shape==PSHAPE_SLOPE){
                int16_t cx=(int16_t)(x+PLAYER_W/2);if(cx<z->x0||cx>z->x1)continue;floor=slope_y(z,cx);
                if(old_bottom<=floor+7&&new_bottom>=floor-1&&floor<best){best=floor;hit=1;gp=255;}
            }
        }
        {int16_t mf;uint8_t mi;if(moving_floor(x,old_bottom,new_bottom,&mf,&mi)&&mf<best){best=mf;hit=1;gp=mi;}}
        if(hit){player.y=(int32_t)(best-PLAYER_H)<<FP_SHIFT;player.vy=0;player.grounded=1;player.ground_platform=gp;return;}
    }else{
        for(i=0;i<PLATFORM_ZONE_COUNT;++i){
            const PlatformZoneDef*z=&platform_zones[i];
            if(z->type!=PCOLL_SOLID||z->shape!=PSHAPE_RECT||!x_intersects_zone(x,PLAYER_W,z))continue;
            if(oldy>=z->y1-4&&newy<=z->y1&&newy+PLAYER_H>z->y0){player.y=(int32_t)z->y1<<FP_SHIFT;player.vy=0;return;}
        }
    }
    player.y=(int32_t)newy<<FP_SHIFT;player.grounded=0;player.ground_platform=255;
}

static uint8_t snap_to_floor(void){
    uint16_t i;int16_t x=px(),feet=(int16_t)(py()+PLAYER_H),best=32767;uint8_t hit=0,gp=255;
    for(i=0;i<PLATFORM_ZONE_COUNT;++i){
        const PlatformZoneDef*z=&platform_zones[i];int16_t floor;
        if(z->type!=PCOLL_SOLID&&z->type!=PCOLL_ONEWAY)continue;
        if(z->shape==PSHAPE_RECT){if(!x_intersects_zone(x,PLAYER_W,z))continue;floor=z->y0;}
        else if(z->shape==PSHAPE_SLOPE){int16_t cx=(int16_t)(x+8);if(cx<z->x0||cx>z->x1)continue;floor=slope_y(z,cx);}else continue;
        if(floor>=feet-5&&floor<=feet+7&&floor<best){best=floor;hit=1;gp=255;}
    }
    {uint8_t m;for(m=0;m<platform_count;++m){MovingPlatform*q=&moving_platforms[m];int16_t floor=q->y;if(x+PLAYER_W>q->x&&x<q->x+16&&floor>=feet-5&&floor<=feet+7&&floor<best){best=floor;hit=1;gp=m;}}}
    if(hit){player.y=(int32_t)(best-PLAYER_H)<<FP_SHIFT;player.vy=0;player.grounded=1;player.ground_platform=gp;return 1u;}return 0u;
}

static void reset_objects(void){uint8_t i;for(i=0;i<collectible_count;++i)collectibles[i].active=1u;for(i=0;i<enemy_count;++i)enemies[i].active=1u;collected=0u;checkpoint_active=0u;checkpoint_x=64u;checkpoint_y=168u;}

static void respawn(uint8_t full_reset){
    if(full_reset)reset_objects();
    player.x=(int32_t)checkpoint_x<<FP_SHIFT;player.y=(int32_t)checkpoint_y<<FP_SHIFT;player.vx=0;player.vy=0;player.grounded=0;player.ground_platform=255;player.dead=0;player.death_timer=0;player.hurt_timer=60u;
    camera_x=(checkpoint_x>112u)?(uint16_t)(checkpoint_x-112u):0u;if(camera_x>PLATFORM_WORLD_W-SCREEN_W)camera_x=PLATFORM_WORLD_W-SCREEN_W;
    target_mode=mode_for_world_x((int16_t)checkpoint_x);rebuild_pending=1u;
}

static void kill_player(void){
    if(player.debug_invincible)return;
    if(player.dead||player.hurt_timer)return;
    if(lives>0u) --lives;
    player.dead=1u;player.death_timer=80u;player.vx=0;player.vy=0;GAME_AUDIO_play(GAME_SFX_DEATH,7u,160);
}

static void hit_player(int16_t enemy_x){
    if(player.debug_invincible||player.dead||player.hurt_timer)return;
    player.hurt_timer=75u;player.vx=(px()<enemy_x)?-1024:1024;player.vy=-900;player.grounded=0;GAME_AUDIO_play(GAME_SFX_HIT,6u,screen_x(enemy_x));
}

static void update_moving_platforms(void){
    uint8_t i;
    for(i=0;i<platform_count;++i){
        MovingPlatform*m=&moving_platforms[i];int16_t ox=m->x,oy=m->y;
        if(m->axis==0u){m->x=(int16_t)(m->x+m->dir);if(m->x>=m->base_x+m->range||m->x<=m->base_x-m->range)m->dir=(int8_t)-m->dir;}
        else{m->y=(int16_t)(m->y+m->dir);if(m->y>=m->base_y+m->range||m->y<=m->base_y-m->range)m->dir=(int8_t)-m->dir;}
        if(player.grounded&&player.ground_platform==i){player.x+=(int32_t)(m->x-ox)<<FP_SHIFT;player.y+=(int32_t)(m->y-oy)<<FP_SHIFT;}
    }
}

static void update_enemies(void){
    uint8_t i;int16_t x=px(),y=py();
    for(i=0;i<enemy_count;++i){Enemy*e=&enemies[i];if(!e->active)continue;e->x=(int16_t)(e->x+e->dir);if(e->x>=e->base_x+e->range||e->x<=e->base_x-e->range)e->dir=(int8_t)-e->dir;
        if(overlap(x,y,PLAYER_W,PLAYER_H,e->x,e->y,16,16)){
            if(player.vy>0&&y+PLAYER_H<=e->y+9){e->active=0u;player.vy=-1050;player.grounded=0;GAME_AUDIO_play(GAME_SFX_HIT,3u,screen_x(e->x));}
            else hit_player(e->x);
        }
    }
}

static void update_interactions(void){
    uint8_t i;int16_t x=px(),y=py(),feet=(int16_t)(y+PLAYER_H);
    for(i=0;i<collectible_count;++i){Collectible*c=&collectibles[i];if(c->active&&overlap(x,y,16,16,c->x,c->y,16,16)){c->active=0u;if(collected<99u)++collected;GAME_AUDIO_play(GAME_SFX_PICKUP,2u,screen_x(c->x));}}
    for(i=0;i<spring_count;++i){Spring*s=&springs[i];if(player.vy>=0&&x+16>s->x&&x<s->x+16&&feet>=s->y&&feet<=s->y+13){player.y=(int32_t)(s->y-PLAYER_H)<<FP_SHIFT;player.vy=SPRING_V;player.grounded=0;player.ground_platform=255;GAME_AUDIO_play(GAME_SFX_SPRING,5u,screen_x(s->x));}}
    for(i=0;i<booster_count;++i){Booster*b=&boosters[i];if(overlap(x,y,16,16,b->x,b->y,16,16)){player.vx=(int32_t)b->direction*BOOST_RUN;GAME_AUDIO_play(GAME_SFX_BOOST,4u,screen_x(b->x));}}
    for(i=0;i<PLATFORM_ZONE_COUNT;++i){const PlatformZoneDef*z=&platform_zones[i];if(z->type==PCOLL_DANGER&&overlap(x,y,16,16,z->x0,z->y0,(int16_t)(z->x1-z->x0),(int16_t)(z->y1-z->y0)))kill_player();
        if(z->type==PCOLL_CHECKPOINT&&!checkpoint_active&&overlap(x,y,16,16,z->x0,z->y0,(int16_t)(z->x1-z->x0),(int16_t)(z->y1-z->y0))){checkpoint_active=1u;checkpoint_x=(uint16_t)(z->x0+40);checkpoint_y=112u;GAME_AUDIO_play(GAME_SFX_CHECKPOINT,5u,160);}
        if(z->type==PCOLL_EXIT&&overlap(x,y,16,16,z->x0,z->y0,(int16_t)(z->x1-z->x0),(int16_t)(z->y1-z->y0))&&finish_timer==0u){finish_timer=150u;player.debug_invincible=1u;GAME_AUDIO_play(GAME_SFX_CHECKPOINT,5u,160);}
    }
    if(y>236){if(player.debug_invincible){player.x=(int32_t)checkpoint_x<<FP_SHIFT;player.y=(int32_t)checkpoint_y<<FP_SHIFT;player.vx=0;player.vy=0;}else kill_player();}
}

static void update_player(uint8_t pad){
    int16_t ox,oy,nx,ny;
    if(player.dead){if(player.death_timer)--player.death_timer;else{if(lives==0u){lives=3u;respawn(1u);}else respawn(0u);}return;}
    if(player.hurt_timer)--player.hurt_timer;
    if(pad&DMS_BUTTON_LEFT){player.vx-=ACCEL;if(player.vx<-MAX_RUN)player.vx=-MAX_RUN;}
    else if(pad&DMS_BUTTON_RIGHT){player.vx+=ACCEL;if(player.vx>MAX_RUN)player.vx=MAX_RUN;}
    else{if(player.vx>0){player.vx-=FRICTION;if(player.vx<0)player.vx=0;}else if(player.vx<0){player.vx+=FRICTION;if(player.vx>0)player.vx=0;}}
    if(PAD_pressed(DMS_BUTTON_A)&&player.grounded){player.vy=JUMP_V;player.grounded=0;player.ground_platform=255;GAME_AUDIO_play(GAME_SFX_JUMP,3u,screen_x(px()));}
    ox=px();oy=py();nx=(int16_t)((player.x+player.vx)>>FP_SHIFT);collide_horizontal(ox,nx,oy);
    if(player.grounded&&player.vy>=0){if(!snap_to_floor())player.grounded=0;}
    if(!player.grounded){player.vy+=GRAVITY;if(player.vy>MAX_FALL)player.vy=MAX_FALL;}
    oy=py();ny=(int16_t)((player.y+player.vy)>>FP_SHIFT);collide_vertical(px(),oy,ny);
    if(px()<0){player.x=0;player.vx=0;}
    if(px()>(int16_t)(PLATFORM_WORLD_W-PLAYER_W)){player.x=(int32_t)(PLATFORM_WORLD_W-PLAYER_W)<<FP_SHIFT;player.vx=0;}
}

static void update_camera(void){
    int16_t p=px(),sw=active_screen_width(),anchor=(current_mode==DMS_MODE_LOW_RES)?88:112;int16_t look=(int16_t)(player.vx>>6);int32_t target=(int32_t)p-anchor+look;int32_t max=PLATFORM_WORLD_W-sw;int32_t diff;
    if(target<0)target=0;
    if(target>max)target=max;
    diff=target-camera_x;
    if(diff>96||diff<-96)camera_x=(uint16_t)((int32_t)camera_x+diff/2);else camera_x=(uint16_t)((int32_t)camera_x+diff/4);
}

static uint8_t player_frame(void){int16_t v=(int16_t)(player.vx>>FP_SHIFT);if(!player.grounded)return player.vy<0?3u:4u;if(iabs16(v)<=1)return 0u;return(uint8_t)(1u+((frame_counter>>2)&1u));}

static void render_player(void){uint8_t i,f=player_frame();int16_t sx=screen_x(px());for(i=0;i<PLAYER_FRAME_SPRITES;++i){if(!player.dead&&i==f)SPR_setPosition(player_frames[i],sx,py());else hide(player_frames[i]);}if(!player.dead&&(player.debug_invincible||player.hurt_timer))SPR_setPosition(shield_sprite,sx,py());else hide(shield_sprite);}
static void render_hud(void){uint8_t i,n=(uint8_t)(collected>99u?99u:collected),mode=current_mode;uint8_t ds[2]={(uint8_t)(n/10u),(uint8_t)(n%10u)};int16_t sw=active_screen_width();
    for(i=0;i<MODE_ICONS;++i){if(i==mode)SPR_setPosition(mode_icons[i],(int16_t)(sw-12),5);else hide(mode_icons[i]);}
    for(i=0;i<3u;++i){if(i<lives)SPR_setPosition(life_icons[i],(int16_t)(sw/2-34+i*18),3);else hide(life_icons[i]);}
    for(i=0;i<HUD_DIGIT_POS;++i){uint8_t d;for(d=0;d<10u;++d){if(d==ds[i])SPR_setPosition(hud_digits[i][d],(int16_t)(6+i*9),5);else hide(hud_digits[i][d]);}}
    for(i=0;i<PERF_LIGHTS;++i){if(i<stream_delta)SPR_setPosition(perf_lights[i],(int16_t)(sw-55+i*9),5);else hide(perf_lights[i]);}}

static void render_sprite_storm(void){
    uint8_t i;
    if(current_mode!=DMS_MODE_SPRITE){for(i=0;i<SPRITE_STORM;++i)hide(sprite_storm[i]);return;}
    for(i=0;i<SPRITE_STORM;++i){
        uint16_t x=(uint16_t)(storm_x[i]+storm_dx[i]);
        uint16_t y=storm_y[i];
        if(x>=312u)x=(uint16_t)(x-312u);
        if(frame_counter&1u){++y;if(y>=208u)y=20u;}
        storm_x[i]=x;storm_y[i]=y;
        SPR_setPosition(sprite_storm[i],(int16_t)x,(int16_t)y);
    }
}

static void render_world(void){uint8_t i;for(i=0;i<collectible_count;++i){if(collectibles[i].active)place_world(collectibles[i].spr,collectibles[i].x,(int16_t)(collectibles[i].y+((frame_counter+i)&3u)-1));else hide(collectibles[i].spr);}for(i=0;i<enemy_count;++i){if(enemies[i].active)place_world(enemies[i].spr,enemies[i].x,enemies[i].y);else hide(enemies[i].spr);}for(i=0;i<spring_count;++i)place_world(springs[i].spr,springs[i].x,springs[i].y);for(i=0;i<booster_count;++i)place_world(boosters[i].spr,boosters[i].x,boosters[i].y);for(i=0;i<platform_count;++i)place_world(moving_platforms[i].spr,moving_platforms[i].x,moving_platforms[i].y);if(checkpoint_active){hide(checkpoint_off);place_world(checkpoint_on,checkpoint_object_x,checkpoint_object_y);}else{hide(checkpoint_on);place_world(checkpoint_off,checkpoint_object_x,checkpoint_object_y);}}

static void scan_objects(void){
    uint8_t i;collectible_count=enemy_count=spring_count=booster_count=platform_count=0u;checkpoint_object_x=2180;checkpoint_object_y=96;
    for(i=0;i<PLATFORM_OBJECT_COUNT;++i){const PlatformObjectDef*o=&platform_objects[i];switch(o->type){
        case POBJ_COLLECTIBLE:if(collectible_count<COLLECTIBLE_MAX){Collectible*c=&collectibles[collectible_count++];c->x=o->x;c->y=o->y;c->active=1u;}break;
        case POBJ_ENEMY:if(enemy_count<ENEMY_MAX){Enemy*e=&enemies[enemy_count];e->base_x=e->x=o->x;e->y=o->y;e->range=o->param1;e->dir=(enemy_count&1u)?1:-1;e->kind=(uint8_t)(enemy_count&1u);e->active=1u;++enemy_count;}break;
        case POBJ_SPRING:if(spring_count<SPRING_MAX){springs[spring_count].x=o->x;springs[spring_count].y=o->y;++spring_count;}break;
        case POBJ_BOOSTER:if(booster_count<BOOSTER_MAX){boosters[booster_count].x=o->x;boosters[booster_count].y=o->y;boosters[booster_count].direction=(int8_t)(o->param1?o->param1:1);++booster_count;}break;
        case POBJ_MOVING_PLATFORM:if(platform_count<PLATFORM_MAX){MovingPlatform*m=&moving_platforms[platform_count];m->base_x=m->x=o->x;m->base_y=m->y=o->y;m->range=o->param1;m->axis=o->param2;m->dir=1;++platform_count;}break;
        case POBJ_CHECKPOINT:checkpoint_object_x=o->x;checkpoint_object_y=o->y;break;
        case POBJ_PLAYER_START:checkpoint_x=(uint16_t)o->x;checkpoint_y=(uint16_t)o->y;break;default:break;}}
}

static void init_objects_and_sprites(void){
    uint8_t i,d;scan_objects();
    /* Slots 0..47 sont ordonnes pour que le MODE 2 (48 sprites) garde tout le gameplay essentiel. */
    for(i=0;i<PLAYER_FRAME_SPRITES;++i)player_frames[i]=SPR_create((uint16_t)(RES_PLAYER_0+i),OFFSCREEN,OFFSCREEN);
    shield_sprite=SPR_create(RES_SHIELD,OFFSCREEN,OFFSCREEN);
    for(i=0;i<MODE_ICONS;++i)mode_icons[i]=SPR_create((uint16_t)(RES_DIGIT_0+i),OFFSCREEN,OFFSCREEN);
    checkpoint_off=SPR_create(RES_CHECKPOINT_OFF,OFFSCREEN,OFFSCREEN);checkpoint_on=SPR_create(RES_CHECKPOINT_ON,OFFSCREEN,OFFSCREEN);
    for(i=0;i<platform_count;++i)moving_platforms[i].spr=SPR_create(RES_PLATFORM,OFFSCREEN,OFFSCREEN);
    for(i=0;i<spring_count;++i)springs[i].spr=SPR_create(RES_SPRING,OFFSCREEN,OFFSCREEN);
    for(i=0;i<booster_count;++i)boosters[i].spr=SPR_create(RES_BOOSTER,OFFSCREEN,OFFSCREEN);
    for(i=0;i<enemy_count;++i)enemies[i].spr=SPR_create(enemies[i].kind?RES_ENEMY_B:RES_ENEMY_A,OFFSCREEN,OFFSCREEN);
    for(i=0;i<16u&&i<collectible_count;++i)collectibles[i].spr=SPR_create(RES_CRYSTAL,OFFSCREEN,OFFSCREEN);
    for(i=0;i<PERF_LIGHTS;++i)perf_lights[i]=SPR_create(RES_DIGIT_8,OFFSCREEN,OFFSCREEN);
    for(i=0;i<3u;++i)life_icons[i]=SPR_create(RES_PLAYER_0,OFFSCREEN,OFFSCREEN);
    for(i=0;i<HUD_DIGIT_POS;++i)for(d=0;d<10u;++d)hud_digits[i][d]=SPR_create((uint16_t)(RES_DIGIT_0+d),OFFSCREEN,OFFSCREEN);
    for(i=16u;i<collectible_count;++i)collectibles[i].spr=SPR_create(RES_CRYSTAL,OFFSCREEN,OFFSCREEN);
    /* Slots 83..127 : charge volontaire du MODE 3.
       V0.3 conserve les 45 sprites mais initialise une fois leurs positions ;
       la boucle chaude n'utilise plus division/modulo. */
    for(i=0;i<SPRITE_STORM;++i){
        uint16_t x=(uint16_t)i*37u;
        uint16_t y=(uint16_t)i*29u;
        while(x>=312u)x=(uint16_t)(x-312u);
        while(y>=188u)y=(uint16_t)(y-188u);
        storm_x[i]=x;storm_y[i]=(uint16_t)(20u+y);storm_dx[i]=(uint8_t)(1u+(i&1u));
        sprite_storm[i]=SPR_create(RES_DIGIT_8,OFFSCREEN,OFFSCREEN);
    }
}

static void request_mode_if_needed(void){uint8_t wanted=mode_for_world_x(px());if(transition_state!=0u)return;if(wanted==current_mode)return;target_mode=wanted;if(wanted==DMS_MODE_LOW_RES||current_mode==DMS_MODE_LOW_RES){transition_state=1u;fade_level=FADE_MAX;}else{transition_state=3u;}}

static void vblank_video_step(void){
    if(rebuild_pending){if(target_mode!=current_mode){current_mode=target_mode;VDP_setMode(current_mode);}PLATFORM_VIDEO_setMode(current_mode,camera_x);PLATFORM_VIDEO_setFade(FADE_MAX);fade_level=FADE_MAX;transition_state=0u;rebuild_pending=0u;}
    if(transition_state==1u){if(fade_level>0u)--fade_level;PLATFORM_VIDEO_setFade(fade_level);if(fade_level==0u)transition_state=2u;}
    else if(transition_state==2u){current_mode=target_mode;VDP_setMode(current_mode);PLATFORM_VIDEO_setMode(current_mode,camera_x);transition_state=4u;}
    else if(transition_state==3u){current_mode=target_mode;VDP_setMode(current_mode);PLATFORM_VIDEO_setMode(current_mode,camera_x);transition_state=0u;}
    else if(transition_state==4u){if(fade_level<FADE_MAX)++fade_level;PLATFORM_VIDEO_setFade(fade_level);if(fade_level>=FADE_MAX)transition_state=0u;}
}


int main(void){
    SYS_init();current_mode=DMS_MODE_STANDARD;target_mode=current_mode;transition_state=0u;fade_level=FADE_MAX;rebuild_pending=0u;
    SYS_waitVBlank();VDP_setMode(current_mode);init_objects_and_sprites();PLATFORM_VIDEO_init(0u,current_mode);GAME_AUDIO_init();lives=3u;collected=0u;checkpoint_active=0u;music_on=1u;finish_timer=0u;frame_counter=0u;player.debug_invincible=0u;respawn(1u);MUS_play(MUSIC_MAIN);
    for(;;){uint8_t pad=PAD_read();GAME_AUDIO_tick();
        if(PAD_pressed(DMS_BUTTON_B)){player.debug_invincible^=1u;}
        if(PAD_pressed(DMS_BUTTON_C)){music_on^=1u;if(music_on)MUS_play(MUSIC_MAIN);else MUS_stop();}
        if(PAD_pressed(DMS_BUTTON_START)){respawn(0u);}
        if(finish_timer){--finish_timer;if(finish_timer==0u){player.debug_invincible=0u;checkpoint_x=64u;checkpoint_y=168u;checkpoint_active=0u;respawn(0u);}}
        if(transition_state==0u&&!rebuild_pending){update_moving_platforms();update_player(pad);if(!player.dead){update_enemies();update_interactions();}update_camera();request_mode_if_needed();}
        ++frame_counter;SYS_waitVBlank();vblank_video_step();
        {uint16_t before=PLATFORM_VIDEO_streamedColumns();PLATFORM_VIDEO_setCamera(camera_x,current_mode);PLATFORM_VIDEO_tick(current_mode,frame_counter);last_stream_count=PLATFORM_VIDEO_streamedColumns();stream_delta=(uint8_t)(last_stream_count-before);if(stream_delta>PERF_LIGHTS)stream_delta=PERF_LIGHTS;}
        render_world();render_player();render_sprite_storm();render_hud();
    }
}

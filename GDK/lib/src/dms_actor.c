#include <stdint.h>
#include "dms_actor.h"
#include "dms_audio.h"
#include "dms_collision.h"
#include "dms_pad.h"
#include "dms_sprite.h"
#include "dms_resource_runtime.h"

#define ACTOR_COUNT 16u
#define Q8(v) ((int32_t)(v) << 8)

typedef struct {
    uint8_t used;
    const DmsActorResourceDesc *res;
    DmsSprite sprite;
    int32_t xq,yq,vxq,vyq;
    int16_t spawn_x,spawn_y;
    uint16_t state;
    uint16_t anim_pos;
    uint32_t anim_tick;
    uint16_t state_ticks;
    uint8_t on_ground,hit_wall,hit_ceiling,anim_done;
    uint8_t coyote_left,jump_buffer_left,jumps_used;
    uint8_t direction,group_mask,projectile_damage,projectile_flags;
    uint16_t hp,invincible_left,stun_left,death_left,life_left;
    uint16_t attack_cooldown,projectile_cooldown,owner;
    uint8_t dying;
} ActorSlot;
static ActorSlot g_actor[ACTOR_COUNT];

extern void dms_coll_resolve_x(int16_t*,int16_t,int16_t,int16_t,int16_t,int16_t,int16_t,int16_t,uint8_t,uint8_t*);
extern void dms_coll_resolve_y(int16_t,int16_t*,int16_t,int16_t,int16_t,int16_t,int16_t,int16_t,uint8_t,uint8_t*,uint8_t*);
extern uint8_t dms_coll_events(int16_t,int16_t,int16_t,int16_t,int16_t,int16_t,uint8_t);
extern uint8_t dms_coll_solid_point(int16_t,int16_t,uint8_t);

static void set_state(ActorSlot *a,uint16_t st);
static int32_t abs32(int32_t value);

static ActorSlot *player_actor(void){
    uint16_t i;for(i=0u;i<ACTOR_COUNT;++i)if(g_actor[i].used&&g_actor[i].res->player_controlled)return &g_actor[i];return 0;
}

static uint8_t actor_index(const ActorSlot *a){return (uint8_t)(a-g_actor);}

static void actor_respawn(ActorSlot *a){
    a->xq=Q8(a->spawn_x);a->yq=Q8(a->spawn_y);a->vxq=0;a->vyq=0;
    a->on_ground=0u;a->hit_wall=0u;a->hit_ceiling=0u;a->anim_done=0u;
    a->coyote_left=0u;a->jump_buffer_left=0u;a->jumps_used=0u;
    a->hp=a->res->hp_start;a->invincible_left=0u;a->stun_left=0u;a->death_left=0u;a->dying=0u;
    set_state(a,a->res->initial_state);
    SPR_setPosition(a->sprite,a->spawn_x,a->spawn_y);
}

static const DmsActorResourceDesc *find_actor(uint16_t id){
    uint16_t i;for(i=0u;i<dms_actor_resource_count;++i)if(dms_actor_resources[i].resource_id==id)return &dms_actor_resources[i];return 0;
}
static int16_t approach(int16_t v,int16_t target,int16_t step){
    if(step<0)step=(int16_t)-step;
    if(v<target){int32_t n=(int32_t)v+step;return (int16_t)(n>target?target:n);}
    if(v>target){int32_t n=(int32_t)v-step;return (int16_t)(n<target?target:n);}
    return v;
}
static uint8_t compare_q8(int16_t a,uint8_t op,int16_t b){
    switch(op){case DMS_ACT_OP_EQ:return a==b;case DMS_ACT_OP_NE:return a!=b;case DMS_ACT_OP_LT:return a<b;case DMS_ACT_OP_LE:return a<=b;case DMS_ACT_OP_GT:return a>b;case DMS_ACT_OP_GE:return a>=b;default:return 0u;}
}
static const DmsSpriteFrameDesc *frame_desc(ActorSlot *a){
    const DmsDresResourceDesc *sr=dms_sprite_desc_for_handle(a->sprite);
    uint16_t f=dms_sprite_get_frame(a->sprite);
    if(!sr || f==0xFFFFu || f>=sr->frame_count) return 0;
    return &sr->frames[f];
}
static void set_state(ActorSlot *a,uint16_t st){
    const DmsActorStateDesc *s;
    const DmsDresResourceDesc *sr;
    const DmsSpriteAnimationDesc *an;
    const DmsSpriteFrameDesc *oldf=frame_desc(a),*newf;
    int16_t old_bottom=0,new_bottom=0;
    if(st>=a->res->state_count)return;
    if(a->state<a->res->state_count && a->res->states[a->state].sfx_exit!=0xFFFFu)
        SFX_play(a->res->states[a->state].sfx_exit);
    if(oldf)old_bottom=(int16_t)(oldf->body_y+oldf->body_h-oldf->pivot_y);
    a->state=st;a->state_ticks=0u;a->anim_pos=0u;a->anim_tick=0u;a->anim_done=0u;
    s=&a->res->states[st];sr=dms_sprite_desc_for_handle(a->sprite);
    if(s->sfx_enter!=0xFFFFu)SFX_play(s->sfx_enter);
    if(!sr)return;
    if(s->animation_id<sr->animation_count){
        an=&sr->animations[s->animation_id];
        if(!an->frame_count)return;
        dms_sprite_set_frame(a->sprite,sr->animation_frame_ids[an->first_frame_index]);
    } else if(s->frame_count && s->first_frame<sr->frame_count){
        dms_sprite_set_frame(a->sprite,s->first_frame);
    } else return;
    newf=frame_desc(a);
    if(a->on_ground && oldf && newf){
        new_bottom=(int16_t)(newf->body_y+newf->body_h-newf->pivot_y);
        a->yq+=Q8((int16_t)(old_bottom-new_bottom));
        SPR_setPosition(a->sprite,(int16_t)(a->xq>>8),(int16_t)(a->yq>>8));
    }
}
static void animation_update(ActorSlot *a){
    const DmsActorStateDesc *s=&a->res->states[a->state];
    const DmsDresResourceDesc *sr=dms_sprite_desc_for_handle(a->sprite);
    const DmsSpriteAnimationDesc *an;
    const DmsSpriteFrameDesc *f;
    uint16_t fid;
    if(!sr)return;
    if(s->animation_id<sr->animation_count){
        an=&sr->animations[s->animation_id];if(!an->frame_count)return;
        fid=sr->animation_frame_ids[an->first_frame_index+a->anim_pos];
    } else {
        if(!s->frame_count || s->first_frame>=sr->frame_count)return;
        fid=(uint16_t)(s->first_frame+a->anim_pos);
        if(fid>=sr->frame_count)return;
        an=0;
    }
    f=&sr->frames[fid];
    a->anim_tick+=(uint16_t)(s->animation_speed_q8>0?s->animation_speed_q8:256);
    if(a->anim_tick<(uint32_t)f->duration_ticks*256u)return;
    a->anim_tick-=(uint32_t)f->duration_ticks*256u;
    if(a->anim_pos+1u<(an?an->frame_count:s->frame_count)){++a->anim_pos;fid=an?sr->animation_frame_ids[an->first_frame_index+a->anim_pos]:(uint16_t)(s->first_frame+a->anim_pos);dms_sprite_set_frame(a->sprite,fid);}
    else if(s->loop){a->anim_pos=0u;fid=an?sr->animation_frame_ids[an->first_frame_index]:s->first_frame;dms_sprite_set_frame(a->sprite,fid);}
    else a->anim_done=1u;
}
static uint8_t condition_true(ActorSlot *a,const DmsActorTransitionDesc *t,uint8_t pad){
    int16_t lhs=0;
    switch(t->condition){
        case DMS_ACT_COND_ALWAYS:return 1u;
        case DMS_ACT_COND_INPUT_X: lhs=(pad&DMS_BUTTON_RIGHT)?256:((pad&DMS_BUTTON_LEFT)?-256:0);break;
        case DMS_ACT_COND_BUTTON:
            lhs=((((pad&t->button_mask)==t->button_mask) && PAD_pressed(t->button_mask)) ||
                 (t->button_mask==a->res->jump_button_mask && a->jump_buffer_left>0u))?256:0;
            break;
        case DMS_ACT_COND_BUTTON_RELEASED:
            lhs=(((pad&t->button_mask)!=t->button_mask) && PAD_released(t->button_mask))?256:0;break;
        case DMS_ACT_COND_BUTTON_HELD:
            lhs=((pad&t->button_mask)==t->button_mask)?256:0;break;
        case DMS_ACT_COND_VELOCITY_X: lhs=(int16_t)a->vxq;break;
        case DMS_ACT_COND_VELOCITY_Y: lhs=(int16_t)a->vyq;break;
        case DMS_ACT_COND_ON_GROUND: lhs=a->on_ground?256:0;break;
        case DMS_ACT_COND_NOT_GROUND: lhs=a->on_ground?0:256;break;
        case DMS_ACT_COND_HIT_WALL: lhs=a->hit_wall?256:0;break;
        case DMS_ACT_COND_HIT_CEILING: lhs=a->hit_ceiling?256:0;break;
        case DMS_ACT_COND_HP: lhs=(int16_t)(a->hp>127u?32767u:a->hp*256u);break;
        case DMS_ACT_COND_TIME_STATE: lhs=(int16_t)(a->state_ticks*256u);break;
        case DMS_ACT_COND_ANIM_DONE: lhs=a->anim_done?256:0;break;
        case DMS_ACT_COND_PLAYER_DISTANCE:{
            uint16_t i;int32_t dx,dy,dist=0x7FFF;
            for(i=0u;i<ACTOR_COUNT;++i)if(g_actor[i].used && g_actor[i].res->player_controlled){
                dx=g_actor[i].xq-a->xq;if(dx<0)dx=-dx;
                dy=g_actor[i].yq-a->yq;if(dy<0)dy=-dy;
                dist=dx>dy?dx:dy;break;
            }
            lhs=(int16_t)(dist>32767?32767:dist);break;
        }
        case DMS_ACT_COND_PLAYER_DISTANCE_X:
        case DMS_ACT_COND_PLAYER_DISTANCE_Y:{
            ActorSlot *player=player_actor();int32_t distance=32767;
            if(player){distance=(t->condition==DMS_ACT_COND_PLAYER_DISTANCE_X)?abs32(player->xq-a->xq):abs32(player->yq-a->yq);}
            lhs=(int16_t)(distance>32767?32767:distance);break;
        }
        default:return 0u;
    }
    if(!compare_q8(lhs,t->op,t->value_q8)) return 0u;
    if(t->action==DMS_ACT_ACTION_JUMP){
        if(a->jumps_used==0u){
            if(!a->on_ground && a->coyote_left==0u) return 0u;
        } else if(a->jumps_used>=a->res->max_jumps) return 0u;
    }
    return 1u;
}
static void transitions_update(ActorSlot *a,uint8_t pad){
    uint16_t i,best=0xFFFFu;uint8_t bestp=255u;
    for(i=0u;i<a->res->transition_count;++i){
        const DmsActorTransitionDesc *t=&a->res->transitions[i];
        if(t->source_state!=a->state)continue;
        if(condition_true(a,t,pad) && (best==0xFFFFu || t->priority<bestp)){best=i;bestp=t->priority;}
    }
    if(best!=0xFFFFu){
        const DmsActorTransitionDesc *t=&a->res->transitions[best];
        if(t->action==DMS_ACT_ACTION_JUMP){
            a->vyq=a->res->jump_vy_q8;
            if(a->jumps_used<255u) ++a->jumps_used;
            a->coyote_left=0u;
            a->jump_buffer_left=0u;
        }
        set_state(a,t->dest_state);
    }
}
static int32_t abs32(int32_t value){return value<0?-value:value;}
static void ai_update(ActorSlot *a){
    ActorSlot *player;
    const DmsSpriteFrameDesc *f;
    int16_t desired=0,speed=a->res->ai_patrol_speed_q8;
    int32_t dx=0,dy=0;
    uint8_t chase=0u;
    if(a->res->player_controlled || a->res->ai_mode==DMS_ACT_AI_NONE || a->stun_left || a->dying)return;
    if(!speed)speed=a->res->max_vx_q8;
    player=player_actor();
    if(player){
        dx=player->xq-a->xq;dy=player->yq-a->yq;
        if(a->res->ai_mode==DMS_ACT_AI_CHASE || a->res->ai_mode==DMS_ACT_AI_BOSS)
            chase=(uint8_t)(!a->res->ai_detection_px || abs32(dx)<=((int32_t)a->res->ai_detection_px<<8));
    }
    if(a->res->ai_mode==DMS_ACT_AI_SHMUP){
        if(!a->direction)a->direction=2u;
        desired=a->direction==1u?speed:(int16_t)-speed;
        if(player && (a->res->ai_flags&DMS_ACT_AI_FOLLOW_Y))
            a->vyq=approach((int16_t)a->vyq,abs32(dy)<Q8(2)?0:(dy<0?(int16_t)-speed:speed),a->res->accel_y_q8);
    }else if((chase || a->res->ai_mode==DMS_ACT_AI_RPG) && player){
        if(a->res->ai_flags&DMS_ACT_AI_FOLLOW_X){a->direction=dx<0?2u:1u;desired=abs32(dx)<Q8(2)?0:(a->direction==1u?speed:(int16_t)-speed);}
        if((a->res->ai_flags&DMS_ACT_AI_FOLLOW_Y) && (a->res->movement_axis==DMS_ACT_MOVE_VERTICAL || a->res->movement_axis==DMS_ACT_MOVE_FREE_2D || a->res->movement_axis==DMS_ACT_MOVE_4_DIR || a->res->movement_axis==DMS_ACT_MOVE_8_DIR))
            a->vyq=approach((int16_t)a->vyq,abs32(dy)<Q8(2)?0:(dy<0?(int16_t)-speed:speed),a->res->accel_y_q8);
    }else if(a->res->ai_mode==DMS_ACT_AI_PATROL || a->res->ai_mode==DMS_ACT_AI_BOSS || a->res->ai_mode==DMS_ACT_AI_CHASE){
        if(!a->direction)a->direction=1u;
        if((a->res->ai_flags&DMS_ACT_AI_TURN_WALL) && a->hit_wall)a->direction=a->direction==1u?2u:1u;
        if(a->res->ai_patrol_distance_px){
            int32_t travelled=abs32(a->xq-Q8(a->spawn_x));
            if(travelled>((int32_t)a->res->ai_patrol_distance_px<<8))a->direction=a->xq>Q8(a->spawn_x)?2u:1u;
        }
        f=frame_desc(a);
        if((a->res->ai_flags&DMS_ACT_AI_TURN_EDGE) && f){
            int16_t x=(int16_t)(a->xq>>8),y=(int16_t)(a->yq>>8);
            int16_t ahead=(int16_t)(x+(a->direction==1u?(f->body_w+4):-4));
            int16_t foot=(int16_t)(y+f->body_y-f->pivot_y+f->body_h+2);
            if(!dms_coll_solid_point(ahead,foot,a->group_mask))a->direction=a->direction==1u?2u:1u;
        }
        desired=a->direction==1u?speed:(int16_t)-speed;
    }
    a->vxq=approach((int16_t)a->vxq,desired,a->res->accel_x_q8);
}
static void physics_update(ActorSlot *a,uint8_t pad){
    const DmsActorStateDesc *s=&a->res->states[a->state];
    const DmsSpriteFrameDesc *f=frame_desc(a);
    int16_t oldx=(int16_t)(a->xq>>8),oldy=(int16_t)(a->yq>>8),nx,ny;
    int16_t vx=(int16_t)a->vxq,vy=(int16_t)a->vyq;
    int16_t maxvx=(int16_t)(((int32_t)a->res->max_vx_q8*s->speed_mul_q8)>>8);
    int16_t maxvy=(int16_t)(((int32_t)a->res->max_vy_q8*s->speed_mul_q8)>>8);
    int16_t accelx=(int16_t)(((int32_t)a->res->accel_x_q8*s->speed_mul_q8)>>8);
    int16_t accely=(int16_t)(((int32_t)a->res->accel_y_q8*s->speed_mul_q8)>>8);
    int16_t brakex=a->res->brake_x_q8,brakey=a->res->brake_y_q8;
    uint8_t allow_x=(uint8_t)(a->res->movement_axis==DMS_ACT_MOVE_HORIZONTAL || a->res->movement_axis==DMS_ACT_MOVE_FREE_2D || a->res->movement_axis==DMS_ACT_MOVE_4_DIR || a->res->movement_axis==DMS_ACT_MOVE_8_DIR);
    uint8_t allow_y=(uint8_t)(a->res->movement_axis==DMS_ACT_MOVE_VERTICAL || a->res->movement_axis==DMS_ACT_MOVE_FREE_2D || a->res->movement_axis==DMS_ACT_MOVE_4_DIR || a->res->movement_axis==DMS_ACT_MOVE_8_DIR);
    uint8_t left=(pad&DMS_BUTTON_LEFT)?1u:0u,right=(pad&DMS_BUTTON_RIGHT)?1u:0u,up=(pad&DMS_BUTTON_UP)?1u:0u,down=(pad&DMS_BUTTON_DOWN)?1u:0u;
    a->hit_wall=a->hit_ceiling=0u;a->on_ground=0u;
    if(a->res->player_controlled && s->controllable && !a->stun_left && !a->dying){
        if(!a->res->allow_diagonal && (left||right) && (up||down))up=down=0u;
        if(a->res->allow_diagonal && (left||right) && (up||down)){maxvx=(int16_t)(((int32_t)maxvx*181)>>8);maxvy=(int16_t)(((int32_t)maxvy*181)>>8);}
        if(allow_x){
            if(left && !right)vx=approach(vx,(int16_t)-maxvx,accelx);
            else if(right && !left)vx=approach(vx,maxvx,accelx);
            else vx=approach(vx,0,brakex);
        }else vx=approach(vx,0,brakex);
        if(allow_y && !s->gravity){
            if(up && !down)vy=approach(vy,(int16_t)-maxvy,accely);
            else if(down && !up)vy=approach(vy,maxvy,accely);
            else vy=approach(vy,0,brakey);
        }else if(!s->gravity)vy=approach(vy,0,brakey);
    }
    if(s->gravity){vy=(int16_t)(vy+a->res->gravity_q8);if(vy>a->res->max_fall_q8)vy=a->res->max_fall_q8;}
    a->vxq=vx;a->vyq=vy;a->xq+=vx;nx=(int16_t)(a->xq>>8);ny=oldy;
    if(f && s->collision_world && f->body_w>0 && f->body_h>0){
        int16_t bx=(int16_t)(f->body_x-f->pivot_x),by=(int16_t)(f->body_y-f->pivot_y);
        dms_coll_resolve_x(&nx,ny,oldx,bx,by,f->body_w,f->body_h,vx,a->group_mask,&a->hit_wall);
        if(nx!=(int16_t)(a->xq>>8)){a->xq=Q8(nx);a->vxq=0;vx=0;}
    }
    a->yq+=vy;ny=(int16_t)(a->yq>>8);nx=(int16_t)(a->xq>>8);
    if(f && s->collision_world && f->body_w>0 && f->body_h>0){
        int16_t bx=(int16_t)(f->body_x-f->pivot_x),by=(int16_t)(f->body_y-f->pivot_y);
        dms_coll_resolve_y(nx,&ny,oldy,bx,by,f->body_w,f->body_h,vy,a->group_mask,&a->on_ground,&a->hit_ceiling);
        if(ny!=(int16_t)(a->yq>>8)){a->yq=Q8(ny);a->vyq=0;}
        {
            uint8_t coll_event=dms_coll_events(nx,ny,bx,by,f->body_w,f->body_h,a->group_mask);
            if(a->res->respawn_on_danger && coll_event==DMS_COLL_EVENT_DANGER){
                COLL_clearEvent();
                actor_respawn(a);
                return;
            }
        }
        if(a->res->respawn_on_danger){
            uint16_t ww=dms_coll_world_width(),wh=dms_coll_world_height();
            int16_t ax0=(int16_t)(nx+bx),ay0=(int16_t)(ny+by);
            int16_t ax1=(int16_t)(ax0+f->body_w);
            if((wh && ay0>(int16_t)(wh+32u)) ||
               (ww && (ax1<-32 || ax0>(int16_t)(ww+32u)))){
                actor_respawn(a);
                return;
            }
        }
    }
    if(vx && !(s->flags&DMS_ACT_STATE_LOCK_DIRECTION)){
        a->direction=vx>0?1u:2u;SPR_setFlipX(a->sprite,(uint8_t)(a->direction==2u));
    }
    if(a->on_ground){
        a->coyote_left=a->res->coyote_ticks;
        a->jumps_used=0u;
    } else if(a->coyote_left>0u) --a->coyote_left;
    SPR_setPosition(a->sprite,(int16_t)(a->xq>>8),(int16_t)(a->yq>>8));
}

static uint8_t actor_bounds(ActorSlot *a,int16_t *x0,int16_t *y0,int16_t *x1,int16_t *y1){
    const DmsSpriteFrameDesc *f=frame_desc(a);
    const DmsActorStateDesc *s;
    int16_t x,y;
    if(!f || a->state>=a->res->state_count)return 0u;
    s=&a->res->states[a->state];if(s->flags&DMS_ACT_STATE_INTANGIBLE)return 0u;
    x=(int16_t)(a->xq>>8);y=(int16_t)(a->yq>>8);
    *x0=(int16_t)(x+f->body_x-f->pivot_x);*y0=(int16_t)(y+f->body_y-f->pivot_y);
    *x1=(int16_t)(*x0+f->body_w);*y1=(int16_t)(*y0+f->body_h);
    return (uint8_t)(f->body_w>0 && f->body_h>0);
}
static uint8_t actors_overlap(ActorSlot *a,ActorSlot *b){
    int16_t ax0,ay0,ax1,ay1,bx0,by0,bx1,by1;
    if(!actor_bounds(a,&ax0,&ay0,&ax1,&ay1)||!actor_bounds(b,&bx0,&by0,&bx1,&by1))return 0u;
    return (uint8_t)(ax0<bx1&&ax1>bx0&&ay0<by1&&ay1>by0);
}
static uint8_t default_target(uint8_t group){
    if(group&DMS_ACTOR_GROUP_PLAYER_PROJECTILE)return DMS_ACTOR_GROUP_ENEMY;
    if(group&DMS_ACTOR_GROUP_ENEMY_PROJECTILE)return DMS_ACTOR_GROUP_PLAYER;
    if(group&DMS_ACTOR_GROUP_PLAYER)return DMS_ACTOR_GROUP_ENEMY;
    if(group&DMS_ACTOR_GROUP_ENEMY)return DMS_ACTOR_GROUP_PLAYER;
    return 0u;
}
static uint8_t damage_slot(ActorSlot *victim,uint8_t amount,ActorSlot *source,int16_t kx,int16_t ky,uint16_t stun,uint8_t pierce){
    const DmsActorStateDesc *state;
    int16_t sign=1;
    if(!victim->used||!victim->hp||!amount||victim->dying)return 0u;
    state=&victim->res->states[victim->state];
    if((victim->invincible_left|| (state->flags&DMS_ACT_STATE_INVULNERABLE))&&!pierce)return 0u;
    victim->hp=victim->hp>amount?(uint16_t)(victim->hp-amount):0u;
    if(source && source->xq>victim->xq)sign=-1;
    if(!kx)kx=victim->res->received_knockback_x_q8;
    if(!ky)ky=victim->res->received_knockback_y_q8;
    victim->vxq=(int32_t)(sign*kx);victim->vyq=ky;victim->stun_left=stun;
    victim->invincible_left=victim->res->invincibility_ticks;
    if(!victim->hp){
        victim->dying=1u;victim->death_left=victim->res->death_delay_ticks;
        if(victim->res->death_state<victim->res->state_count)set_state(victim,victim->res->death_state);
    }else if(victim->res->hurt_state<victim->res->state_count)set_state(victim,victim->res->hurt_state);
    return 1u;
}
static void combat_update(void){
    uint16_t i,j,k;
    for(i=0u;i<ACTOR_COUNT;++i){
        ActorSlot *attacker=&g_actor[i];uint8_t contact,target;
        if(!attacker->used||attacker->dying)continue;
        contact=attacker->projectile_damage?attacker->projectile_damage:attacker->res->contact_damage;
        target=default_target(attacker->group_mask);
        if(contact&&target){
            for(j=0u;j<ACTOR_COUNT;++j){
                ActorSlot *victim=&g_actor[j];if(i==j||!victim->used||victim->owner==i||!(victim->group_mask&target))continue;
                if(actors_overlap(attacker,victim)&&damage_slot(victim,contact,attacker,0,0,0u,0u)){
                    if(attacker->projectile_flags&DMS_ACT_PROJECTILE_DESTROY_HIT){ACTOR_destroy(i);break;}
                }
            }
            if(!attacker->used)continue;
        }
        if(attacker->attack_cooldown)continue;
        for(k=0u;k<attacker->res->attack_count;++k){
            const DmsActorAttackDesc *attack=&attacker->res->attacks[k];uint8_t hit=0u;
            if(attack->state!=attacker->state)continue;
            for(j=0u;j<ACTOR_COUNT;++j){
                ActorSlot *victim=&g_actor[j];if(i==j||!victim->used||!(victim->group_mask&attack->target_mask))continue;
                if(actors_overlap(attacker,victim)&&damage_slot(victim,attack->damage,attacker,attack->knockback_x_q8,attack->knockback_y_q8,attack->stun_ticks,(uint8_t)(attack->flags&1u)))hit=1u;
            }
            if(hit){attacker->attack_cooldown=attack->cooldown_ticks?attack->cooldown_ticks:1u;break;}
        }
    }
}
static uint8_t owned_projectiles(uint16_t owner){
    uint16_t i;uint8_t count=0u;for(i=0u;i<ACTOR_COUNT;++i)if(g_actor[i].used&&g_actor[i].owner==owner)++count;return count;
}
static void projectile_update(ActorSlot *a){
    uint16_t i;DmsActor handle;ActorSlot *shot,*player;uint16_t owner=actor_index(a);
    if(a->projectile_cooldown||a->dying)return;
    for(i=0u;i<a->res->projectile_count;++i){
        const DmsActorProjectileDesc *p=&a->res->projectiles[i];int16_t x,y;int32_t dx=0;
        if(p->actor_resource_id==0xFFFFu || (p->state!=0xFFFFu&&p->state!=a->state))continue;
        if(owned_projectiles(owner)>=p->max_simultaneous)continue;
        x=(int16_t)((a->xq>>8)+(a->direction==2u?-p->offset_x:p->offset_x));y=(int16_t)((a->yq>>8)+p->offset_y);
        handle=ACTOR_spawn(p->actor_resource_id,x,y);if(handle==0xFFFFu)continue;
        shot=&g_actor[handle];shot->owner=owner;shot->group_mask=p->group_mask;shot->projectile_damage=p->damage;shot->projectile_flags=p->flags;shot->life_left=p->life_ticks;
        player=player_actor();if((p->flags&DMS_ACT_PROJECTILE_TOWARD_PLAYER)&&player)dx=player->xq-shot->xq;
        shot->direction=(dx?dx<0:(a->direction==2u))?2u:1u;shot->vxq=shot->direction==2u?-(int32_t)p->speed_q8:p->speed_q8;
        SPR_setFlipX(shot->sprite,(uint8_t)(shot->direction==2u));a->projectile_cooldown=p->cadence_ticks?p->cadence_ticks:1u;break;
    }
}
static uint8_t outside_view(ActorSlot *a){
    int16_t sx=(int16_t)((a->xq>>8)-dms_bg_scroll_x()),sy=(int16_t)((a->yq>>8)-dms_bg_scroll_y());
    int16_t m=a->res->activation_margin;
    return (uint8_t)(sx<-m||sx>320+m||sy<-m||sy>224+m);
}

void dms_actor_runtime_init(void){uint16_t i;for(i=0u;i<ACTOR_COUNT;++i)g_actor[i].used=0u;}
DmsActor ACTOR_spawn(uint16_t id,int16_t x,int16_t y){
    uint16_t i;const DmsActorResourceDesc *r=find_actor(id);DmsSprite sp;
    if(!r) return 0xFFFFu;
    for(i=0u;i<ACTOR_COUNT;++i) if(!g_actor[i].used) break;
    if(i==ACTOR_COUNT) return 0xFFFFu;
    sp=SPR_create(r->sprite_resource_id,x,y);if(sp==0xFFFFu)return 0xFFFFu;
    g_actor[i].used=1u;g_actor[i].res=r;g_actor[i].sprite=sp;g_actor[i].xq=Q8(x);g_actor[i].yq=Q8(y);g_actor[i].vxq=g_actor[i].vyq=0;g_actor[i].state=0xFFFFu;
    g_actor[i].spawn_x=x;g_actor[i].spawn_y=y;g_actor[i].on_ground=0u;g_actor[i].hit_wall=g_actor[i].hit_ceiling=0u;
    g_actor[i].coyote_left=0u;g_actor[i].jump_buffer_left=0u;g_actor[i].jumps_used=0u;
    g_actor[i].direction=1u;g_actor[i].group_mask=r->target_mask;g_actor[i].projectile_damage=0u;g_actor[i].projectile_flags=0u;
    g_actor[i].hp=r->hp_start;g_actor[i].invincible_left=0u;g_actor[i].stun_left=0u;g_actor[i].death_left=0u;g_actor[i].life_left=0u;
    g_actor[i].attack_cooldown=0u;g_actor[i].projectile_cooldown=0u;g_actor[i].owner=0xFFFFu;g_actor[i].dying=0u;
    if(r->collision_resource_id!=0xFFFFu) COLL_bind(r->collision_resource_id);
    set_state(&g_actor[i],r->initial_state);
    return i;
}
void ACTOR_destroy(DmsActor actor){
    if(actor>=ACTOR_COUNT || !g_actor[actor].used)return;
    SPR_destroy(g_actor[actor].sprite);g_actor[actor].used=0u;
}
void ACTOR_setVisible(DmsActor actor,uint8_t visible){if(actor<ACTOR_COUNT&&g_actor[actor].used)SPR_setVisible(g_actor[actor].sprite,visible);}
void ACTOR_setPriority(DmsActor actor,uint8_t in_front){if(actor<ACTOR_COUNT&&g_actor[actor].used)SPR_setPriority(g_actor[actor].sprite,in_front);}
void ACTOR_setPalette(DmsActor actor,uint8_t palette_id){if(actor<ACTOR_COUNT&&g_actor[actor].used)SPR_setPalette(g_actor[actor].sprite,palette_id);}
void ACTOR_update(void){
    uint16_t i;uint8_t pad=PAD_read();
    for(i=0u;i<ACTOR_COUNT;++i) if(g_actor[i].used){
        ActorSlot *a=&g_actor[i];
        if(a->invincible_left)--a->invincible_left;
        if(a->stun_left)--a->stun_left;
        if(a->attack_cooldown)--a->attack_cooldown;
        if(a->projectile_cooldown)--a->projectile_cooldown;
        if(a->life_left && --a->life_left==0u){ACTOR_destroy(i);continue;}
        if(outside_view(a)){
            if((a->res->offscreen_flags&2u)&&!a->res->player_controlled){ACTOR_destroy(i);continue;}
            if(!(a->res->offscreen_flags&1u)){SPR_setVisible(a->sprite,0u);continue;}
        }else SPR_setVisible(a->sprite,1u);
        if(a->res->jump_buffer_ticks && PAD_pressed(a->res->jump_button_mask))
            a->jump_buffer_left=a->res->jump_buffer_ticks;
        ai_update(a);
        physics_update(a,pad);
        if((a->projectile_flags&DMS_ACT_PROJECTILE_DESTROY_HIT)&&(a->hit_wall||a->on_ground)){ACTOR_destroy(i);continue;}
        if(!a->dying&&!a->stun_left)transitions_update(a,pad);
        animation_update(a);
        if(a->state<a->res->state_count&&a->res->states[a->state].duration_ticks&&a->state_ticks>=a->res->states[a->state].duration_ticks)a->anim_done=1u;
        projectile_update(a);
        if(a->jump_buffer_left>0u && !a->on_ground) --a->jump_buffer_left;
        ++a->state_ticks;
        if(a->dying&&a->res->death_destroys){
            if(a->death_left){--a->death_left;if(!a->death_left){ACTOR_destroy(i);continue;}}
            else if(a->res->death_state>=a->res->state_count||a->anim_done){ACTOR_destroy(i);continue;}
        }
    }
    combat_update();
}
int16_t ACTOR_x(DmsActor a){return (a<ACTOR_COUNT&&g_actor[a].used)?(int16_t)(g_actor[a].xq>>8):0;}
int16_t ACTOR_y(DmsActor a){return (a<ACTOR_COUNT&&g_actor[a].used)?(int16_t)(g_actor[a].yq>>8):0;}
uint16_t ACTOR_state(DmsActor a){return (a<ACTOR_COUNT&&g_actor[a].used)?g_actor[a].state:0xFFFFu;}
void ACTOR_damage(DmsActor actor,uint16_t damage){if(actor<ACTOR_COUNT&&g_actor[actor].used)damage_slot(&g_actor[actor],(uint8_t)(damage>255u?255u:damage),0,0,0,0u,1u);}
void ACTOR_heal(DmsActor actor,uint16_t amount){
    ActorSlot *a;if(actor>=ACTOR_COUNT||!g_actor[actor].used)return;a=&g_actor[actor];
    a->hp=(uint16_t)(a->hp+amount<a->hp||a->hp+amount>a->res->hp_max?a->res->hp_max:a->hp+amount);
}
uint16_t ACTOR_hp(DmsActor actor){return (actor<ACTOR_COUNT&&g_actor[actor].used)?g_actor[actor].hp:0u;}
uint8_t ACTOR_isAlive(DmsActor actor){return (uint8_t)(actor<ACTOR_COUNT&&g_actor[actor].used&&g_actor[actor].hp>0u);}
void ACTOR_setVelocity(DmsActor actor,int16_t vx_q8,int16_t vy_q8){if(actor<ACTOR_COUNT&&g_actor[actor].used){g_actor[actor].vxq=vx_q8;g_actor[actor].vyq=vy_q8;}}

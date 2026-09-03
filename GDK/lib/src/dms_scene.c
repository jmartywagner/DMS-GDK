#include <stdint.h>
#include "dms1_hw.h"
#include "dms_actor.h"
#include "dms_audio.h"
#include "dms_bg.h"
#include "dms_fx.h"
#include "dms_flow.h"
#include "dms_gameplay.h"
#include "dms_pad.h"
#include "dms_resource_runtime.h"
#include "dms_scene.h"
#include "dms_sprite.h"
#include "dms_vdp.h"

#define SCENE_BACKEND_NONE 0u
#define SCENE_BACKEND_V1   1u
#define SCENE_BACKEND_V2   2u
#define SCENE_FONT_COUNT   96u
#define SCENE_FONT_BYTES   (SCENE_FONT_COUNT*32u)
#define BG_A_STD_BASE      0x08000u
#define BG_B_STD_BASE      0x09000u
#define BG_A_HIGH_BASE     0x0B000u

typedef struct {
    uint8_t active;
    uint8_t spawned;
    uint8_t ended;
    uint8_t anim_pos;
    uint8_t palette_pos;
    uint8_t enabled;
    uint8_t text_count, typewriter_speed, typewriter_tick, typewriter_active;
    uint8_t sliding;
    int16_t option_value, draw_x_prev, slide_offset;
    uint16_t slide_duration, slide_tick;
    uint16_t anim_tick;
    uint16_t palette_tick;
    int32_t xq, yq;
    uint16_t handle;
} SceneObjectSlot;

static uint8_t g_backend;
static uint8_t g_active;
static uint8_t g_menu_enabled;
static uint8_t g_menu_index;
static uint16_t g_frame;
static uint16_t g_result;
static uint16_t g_event_cursor;
static uint16_t g_current=DMS_SCENE_INVALID;
static uint16_t g_trigger;
static int16_t g_camera_x,g_camera_y;
static int32_t g_camera_xq,g_camera_yq;
static int16_t g_camera_vxq,g_camera_vyq;
static int16_t g_scroll_ax,g_scroll_ay,g_scroll_bx,g_scroll_by;
static uint8_t g_scene_mode;
static uint8_t g_waiting,g_wait_mask;
static const DmsSceneDef *g_v1;
static const DmsSceneResourceDesc *g_v2;
static SceneObjectSlot g_slots[DMS_SCENE_OBJECT_MAX];
static uint8_t g_v1_visible[DMS_SCENE_OBJECT_MAX];
static uint8_t g_v1_count[DMS_SCENE_OBJECT_MAX];
static int16_t g_v1_x[DMS_SCENE_OBJECT_MAX];
static uint16_t g_font_base=DMS_SCENE_INVALID;

static volatile uint8_t * const vram8=(volatile uint8_t*)DMS_VRAM_BASE;
static volatile uint16_t * const vram16=(volatile uint16_t*)DMS_VRAM_BASE;
static volatile uint16_t * const cram16=(volatile uint16_t*)DMS_CRAM_BASE;

/* Scene resource fallbacks live in dms_stubs.c. Keeping the weak zero
   definition out of this translation unit is critical: with -Os GCC can
   otherwise constant-fold SCENE_start() to `return 0` before the linker
   gets a chance to override the weak table with generated resources. */

static const uint8_t g_digits[70]={
14,17,19,21,25,17,14,4,12,4,4,4,4,14,14,17,1,2,4,8,31,30,1,1,14,1,1,30,2,6,10,18,31,2,2,31,16,16,30,1,1,30,14,16,16,30,17,17,14,31,1,2,4,8,8,8,14,17,17,14,17,17,14,14,17,17,15,1,1,14};
static const uint8_t g_letters[182]={
14,17,17,31,17,17,17,30,17,17,30,17,17,30,14,17,16,16,16,17,14,30,17,17,17,17,17,30,31,16,16,30,16,16,31,31,16,16,30,16,16,16,14,17,16,23,17,17,15,17,17,17,31,17,17,17,14,4,4,4,4,4,14,7,2,2,2,18,18,12,17,18,20,24,20,18,17,16,16,16,16,16,16,31,17,27,21,21,17,17,17,17,25,21,19,17,17,17,14,17,17,17,17,17,14,30,17,17,30,16,16,16,14,17,17,17,21,18,13,30,17,17,30,20,18,17,15,16,16,14,1,1,30,31,4,4,4,4,4,4,17,17,17,17,17,17,14,17,17,17,17,17,10,4,17,17,17,21,21,21,10,17,17,10,4,10,17,17,17,17,10,4,4,4,4,31,1,2,4,8,16,31};

static const DmsSceneResourceDesc *find_scene(uint16_t id){
    uint16_t i;for(i=0u;i<dms_scene_resource_count;++i)if(dms_scene_resources[i].resource_id==id)return &dms_scene_resources[i];return 0;
}
static uint32_t plane_a_base(uint8_t mode){return (mode==1u||mode==4u)?BG_A_HIGH_BASE:BG_A_STD_BASE;}
static uint8_t glyph_row(uint8_t ch,uint8_t row){
    if(ch>='a'&&ch<='z')ch=(uint8_t)(ch-32u);
    if(ch>='0'&&ch<='9')return g_digits[(uint16_t)(ch-'0')*7u+row];
    if(ch>='A'&&ch<='Z')return g_letters[(uint16_t)(ch-'A')*7u+row];
    if(ch=='-'&&row==3u)return 31u;
    if(ch=='_'&&row==6u)return 31u;
    if(ch==':'&&(row==2u||row==4u))return 4u;
    if(ch=='.'&&row==6u)return 4u;
    if(ch=='!'&&(row<4u||row==5u))return 4u;
    if(ch=='+'&&(row==3u))return 31u;
    if(ch=='+'&&(row==1u||row==2u||row==4u||row==5u))return 4u;
    return 0u;
}
static void build_builtin_font(void){
    uint16_t tile;uint8_t y,x;
    if(g_font_base!=DMS_SCENE_INVALID)return;
    g_font_base=dms_sprite_reserve_patterns(SCENE_FONT_COUNT);if(g_font_base==DMS_SCENE_INVALID)return;
    for(tile=0u;tile<SCENE_FONT_COUNT;++tile){
        uint8_t ch=(uint8_t)(tile+32u);
        for(y=0u;y<8u;++y){
            uint8_t row=(y<7u)?glyph_row(ch,y):0u;
            for(x=0u;x<4u;++x){
                uint8_t p0=(x*2u>=1u&&x*2u<=5u&&(row&(1u<<(5u-x*2u))))?15u:0u;
                uint8_t p1=(x*2u+1u>=1u&&x*2u+1u<=5u&&(row&(1u<<(5u-(x*2u+1u)))))?15u:0u;
                vram8[(uint32_t)g_font_base*32u+(uint32_t)tile*32u+y*4u+x]=(uint8_t)((p0<<4)|p1);
            }
        }
    }
    cram16[3u*16u+0u]=0u;cram16[3u*16u+15u]=0x01FFu;
}
static void clear_text_cells(uint8_t mode,int16_t x,int16_t y,uint16_t count){
    uint16_t i;volatile uint16_t *plane=vram16+(plane_a_base(mode)>>1);
    int16_t cx=(int16_t)(x>>3),cy=(int16_t)(y>>3);if(cy<0||cy>=32)return;
    for(i=0u;i<count;++i){int16_t q=(int16_t)(cx+(int16_t)i);if(q>=0&&q<64)plane[(uint32_t)(cy&31)*64u+(uint16_t)q]=0u;}
}
static void draw_text(uint8_t mode,const char *text,int16_t x,int16_t y,uint8_t palette,uint8_t priority,uint16_t count){
    uint16_t i;volatile uint16_t *plane;int16_t cx,cy;
    if(!text)return;
    build_builtin_font();
    if(g_font_base==DMS_SCENE_INVALID)return;
    plane=vram16+(plane_a_base(mode)>>1);cx=(int16_t)(x>>3);cy=(int16_t)(y>>3);if(cy<0||cy>=32)return;
    for(i=0u;text[i]&&i<count;++i){uint8_t ch=(uint8_t)text[i];int16_t q=(int16_t)(cx+(int16_t)i);if(ch<32u||ch>=128u)ch='?';if(q>=0&&q<64)plane[(uint32_t)(cy&31)*64u+(uint16_t)q]=(uint16_t)((g_font_base+ch-32u)&0x03FFu)|((uint16_t)(palette&7u)<<10)|(priority?0x2000u:0u);}
}
static uint16_t text_length(const char *s){uint16_t n=0u;if(!s)return 0u;while(s[n]&&n<63u)++n;return n;}

static uint8_t v2_text_object(const DmsSceneObjectResourceDesc *o){
    return (uint8_t)(o->kind==DMS_SCENE_KIND_TEXT || (o->kind==DMS_SCENE_KIND_UI&&o->resource_id==DMS_SCENE_INVALID));
}
static uint8_t v2_selectable(uint16_t i){
    const DmsSceneObjectResourceDesc *o;
    if(!g_v2||i>=g_v2->object_count||i>=DMS_SCENE_OBJECT_MAX)return 0u;
    o=&g_v2->objects[i];
    return (uint8_t)(o->kind==DMS_SCENE_KIND_UI && g_slots[i].enabled && (o->action_event||o->option_type));
}
static void destroy_slots(void){
    uint16_t i;if(!g_v2)return;
    for(i=0u;i<g_v2->object_count&&i<DMS_SCENE_OBJECT_MAX;++i)if(g_slots[i].spawned){
        const DmsSceneObjectResourceDesc *o=&g_v2->objects[i];
        if(o->kind==DMS_SCENE_KIND_ACTOR)ACTOR_destroy(g_slots[i].handle);
        else if(!v2_text_object(o))SPR_destroy(g_slots[i].handle);
        else clear_text_cells(g_scene_mode,o->x,o->y,63u);
        g_slots[i].spawned=0u;
    }
}
void SCENE_stop(void){
    if(g_backend==SCENE_BACKEND_V2){destroy_slots();ACTOR_destroyAll();}
    g_active=0u;g_backend=SCENE_BACKEND_NONE;g_v1=0;g_v2=0;g_current=DMS_SCENE_INVALID;g_result=0u;
}

static void spawn_v2(uint16_t i){
    const DmsSceneObjectResourceDesc *o;SceneObjectSlot *slot;if(!g_v2||i>=g_v2->object_count||i>=DMS_SCENE_OBJECT_MAX)return;
    o=&g_v2->objects[i];slot=&g_slots[i];if(slot->spawned||slot->ended||!slot->enabled)return;
    if(o->start_trigger&&o->start_trigger!=g_trigger)return;
    if(g_frame<o->start_frame)return;
    slot->xq=(int32_t)o->x<<8;slot->yq=(int32_t)o->y<<8;slot->handle=DMS_SCENE_INVALID;slot->palette_pos=0u;slot->palette_tick=0u;slot->draw_x_prev=o->x;slot->text_count=(uint8_t)text_length(o->text);slot->typewriter_active=0u;slot->sliding=0u;
    if(o->kind==DMS_SCENE_KIND_ACTOR){
        slot->handle=ACTOR_spawn(o->resource_id,o->x,o->y);
        if(slot->handle!=DMS_SCENE_INVALID){ACTOR_setVisible(slot->handle,1u);ACTOR_setPriority(slot->handle,o->priority);ACTOR_setPalette(slot->handle,o->palette);ACTOR_setVelocity(slot->handle,o->velocity_x_q8,o->velocity_y_q8);}
    } else if(v2_text_object(o)){
        draw_text(g_scene_mode,o->text,o->x,o->y,o->palette,o->priority,text_length(o->text));slot->handle=0u;
    } else {
        slot->handle=SPR_create(o->resource_id,o->x,o->y);
        if(slot->handle!=DMS_SCENE_INVALID){SPR_setScreenSpace(slot->handle,1u);SPR_setPriority(slot->handle,o->priority);SPR_setPalette(slot->handle,o->palette);SPR_setFlipX(slot->handle,o->direction?1u:0u);SPR_setAnimation(slot->handle,o->animation_id);}
    }
    if(slot->handle!=DMS_SCENE_INVALID){slot->spawned=1u;slot->active=1u;slot->anim_pos=0u;slot->anim_tick=0u;}
}
static void hide_v2(uint16_t i){
    const DmsSceneObjectResourceDesc *o;SceneObjectSlot *slot;
    if(!g_v2||i>=g_v2->object_count||i>=DMS_SCENE_OBJECT_MAX)return;
    o=&g_v2->objects[i];slot=&g_slots[i];
    if(slot->spawned){if(o->kind==DMS_SCENE_KIND_ACTOR)ACTOR_destroy(slot->handle);else if(v2_text_object(o))clear_text_cells(g_scene_mode,slot->draw_x_prev,o->y,96u);else SPR_destroy(slot->handle);}
    slot->spawned=0u;slot->active=0u;slot->enabled=0u;
}
static void palette_update_v2(const DmsSceneObjectResourceDesc *o,SceneObjectSlot *slot){
    uint8_t palette;
    if(o->palette_animation!=DMS_SCENE_PALETTE_CYCLE||o->palette_span<2u)return;
    if(++slot->palette_tick<(o->palette_cadence?o->palette_cadence:1u))return;
    slot->palette_tick=0u;slot->palette_pos=(uint8_t)((slot->palette_pos+1u)%o->palette_span);palette=(uint8_t)(o->palette+slot->palette_pos);
    if(o->kind==DMS_SCENE_KIND_ACTOR)ACTOR_setPalette(slot->handle,palette);else if(!v2_text_object(o))SPR_setPalette(slot->handle,palette);
}
static void animate_v2(const DmsSceneObjectResourceDesc *o,SceneObjectSlot *slot){
    const DmsDresResourceDesc *r;const DmsSpriteAnimationDesc *a;uint16_t cadence;
    if(o->kind==DMS_SCENE_KIND_ACTOR||v2_text_object(o))return;
    r=dms_sprite_desc_for_handle(slot->handle);if(!r||o->animation_id>=r->animation_count)return;a=&r->animations[o->animation_id];if(a->frame_count<2u)return;
    cadence=o->animation_cadence;if(!cadence){uint16_t fid=r->animation_frame_ids[a->first_frame_index+slot->anim_pos];cadence=r->frames[fid].duration_ticks;if(!cadence)cadence=1u;}
    if(++slot->anim_tick<cadence)return;
    slot->anim_tick=0u;
    if((uint16_t)slot->anim_pos+1u<a->frame_count)++slot->anim_pos;else if(o->loop)slot->anim_pos=0u;else return;
    dms_sprite_set_frame(slot->handle,r->animation_frame_ids[a->first_frame_index+slot->anim_pos]);
}
static void end_v2(uint16_t i){
    if(!g_v2||i>=g_v2->object_count||i>=DMS_SCENE_OBJECT_MAX)return;
    hide_v2(i);g_slots[i].ended=1u;
}
static void uint_text(uint16_t value,char *buf){
    char tmp[6];uint8_t n=0u,i;if(!value){buf[0]='0';buf[1]=0;return;}while(value&&n<5u){tmp[n++]=(char)('0'+value%10u);value=(uint16_t)(value/10u);}for(i=0u;i<n;++i)buf[i]=tmp[n-1u-i];buf[n]=0;
}
static void draw_v2_text(uint16_t i){
    const DmsSceneObjectResourceDesc *o=&g_v2->objects[i];SceneObjectSlot *slot=&g_slots[i];uint8_t palette=(uint8_t)(o->palette+slot->palette_pos);char buf[6];int16_t draw_x=(int16_t)(slot->xq>>8);uint16_t count=slot->typewriter_active?slot->text_count:text_length(o->text);
    if(g_menu_enabled&&i==g_menu_index)palette=o->selected_palette;
    clear_text_cells(g_scene_mode,slot->draw_x_prev,o->y,96u);draw_text(g_scene_mode,o->text,draw_x,o->y,palette,o->priority,count);slot->draw_x_prev=draw_x;
    if(o->option_type){uint_text((uint16_t)(slot->option_value<0?0:slot->option_value),buf);draw_text(g_scene_mode,buf,(int16_t)(draw_x+(int16_t)text_length(o->text)*8+8),o->y,palette,o->priority,5u);}
}
static void menu_find_first(void){uint16_t i;g_menu_index=0u;for(i=0u;i<g_v2->object_count&&i<DMS_SCENE_OBJECT_MAX;++i)if(v2_selectable(i)){g_menu_index=(uint8_t)i;return;}}
static void menu_move(int8_t direction){
    uint16_t step,i=(uint16_t)g_menu_index;if(!g_v2||!g_v2->object_count)return;
    for(step=0u;step<g_v2->object_count;++step){i=(uint16_t)((i+g_v2->object_count+(direction<0?-1:1))%g_v2->object_count);if(v2_selectable(i)){g_menu_index=(uint8_t)i;SFX_play(g_v2->menu_move_sfx);return;}}
}
static void menu_adjust(int8_t direction){
    const DmsSceneObjectResourceDesc *o;SceneObjectSlot *slot;int32_t value,step;
    if(!v2_selectable(g_menu_index))return;
    o=&g_v2->objects[g_menu_index];slot=&g_slots[g_menu_index];
    if(!o->option_type)return;
    step=o->option_step?o->option_step:1;value=(int32_t)slot->option_value+(direction<0?-step:step);
    if(value<o->option_min)value=o->option_max;
    if(value>o->option_max)value=o->option_min;
    slot->option_value=(int16_t)value;
    if(o->option_type==DMS_SCENE_OPTION_LIVES)GAMEPLAY_setStartingLives((uint16_t)(value>0?value:1));
    SFX_play(g_v2->menu_move_sfx);
}
static void menu_update_v2(void){
    const DmsSceneObjectResourceDesc *o;SceneObjectSlot *slot;if(!g_menu_enabled)return;
    if(PAD_pressed(DMS_BUTTON_UP))menu_move(-1);
    if(PAD_pressed(DMS_BUTTON_DOWN))menu_move(1);
    if(PAD_pressed(DMS_BUTTON_LEFT))menu_adjust(-1);
    if(PAD_pressed(DMS_BUTTON_RIGHT))menu_adjust(1);
    if(!PAD_pressed(DMS_BUTTON_A|DMS_BUTTON_START)||!v2_selectable(g_menu_index))return;
    o=&g_v2->objects[g_menu_index];slot=&g_slots[g_menu_index];
    SFX_play(g_v2->menu_validate_sfx);
    if(o->option_type==DMS_SCENE_OPTION_MUSIC_TEST)MUS_play((uint16_t)(slot->option_value<0?0:slot->option_value));
    else if(o->option_type==DMS_SCENE_OPTION_SFX_TEST)SFX_play((uint16_t)(slot->option_value<0?0:slot->option_value));
    if(o->action_event){g_result=o->action_event;FLOW_emit(o->action_event);g_active=0u;}
}
static void spawn_formation(const DmsSceneRuntimeEventDesc *e){
    const DmsSceneObjectResourceDesc *o;uint8_t n,j;if(!g_v2||e->target>=g_v2->object_count)return;o=&g_v2->objects[e->target];if(o->kind!=DMS_SCENE_KIND_ACTOR)return;n=(uint8_t)(e->a<1?1:(e->a>64?64:e->a));
    for(j=0u;j<n;++j){DmsActor h=ACTOR_spawn(o->resource_id,(int16_t)(o->x+j*e->b),(int16_t)(o->y+j*e->c));if(h!=DMS_SCENE_INVALID){int32_t scale=e->d?e->d:256;ACTOR_setPriority(h,o->priority);ACTOR_setPalette(h,o->palette);ACTOR_setVelocity(h,(int16_t)(((int32_t)o->velocity_x_q8*scale)>>8),(int16_t)(((int32_t)o->velocity_y_q8*scale)>>8));}}
}
static void start_typewriter(uint16_t target,int16_t speed){
    SceneObjectSlot *slot;const DmsSceneObjectResourceDesc *o;if(!g_v2||target>=g_v2->object_count||target>=DMS_SCENE_OBJECT_MAX)return;o=&g_v2->objects[target];slot=&g_slots[target];slot->enabled=1u;slot->ended=0u;spawn_v2(target);if(!v2_text_object(o))return;slot->typewriter_active=1u;slot->typewriter_speed=(uint8_t)(speed<1?1:(speed>255?255:speed));slot->typewriter_tick=0u;slot->text_count=0u;clear_text_cells(g_scene_mode,slot->draw_x_prev,o->y,96u);
}
static void start_slide(uint16_t target,int16_t offset,int16_t duration){
    SceneObjectSlot *slot;const DmsSceneObjectResourceDesc *o;if(!g_v2||target>=g_v2->object_count||target>=DMS_SCENE_OBJECT_MAX)return;o=&g_v2->objects[target];slot=&g_slots[target];slot->enabled=1u;slot->ended=0u;spawn_v2(target);if(!slot->spawned)return;slot->sliding=1u;slot->slide_offset=offset;slot->slide_duration=(uint16_t)(duration<1?1:duration);slot->slide_tick=0u;slot->xq=(int32_t)(o->x+offset)*256;if(o->kind==DMS_SCENE_KIND_ACTOR)ACTOR_setPosition(slot->handle,(int16_t)(slot->xq>>8),(int16_t)(slot->yq>>8));else if(!v2_text_object(o))SPR_setPosition(slot->handle,(int16_t)(slot->xq>>8),(int16_t)(slot->yq>>8));
}
static void update_slot_effects(uint16_t i){
    SceneObjectSlot *slot=&g_slots[i];const DmsSceneObjectResourceDesc *o=&g_v2->objects[i];
    if(slot->typewriter_active&&slot->text_count<text_length(o->text)){if(++slot->typewriter_tick>=slot->typewriter_speed){slot->typewriter_tick=0u;++slot->text_count;}}
    if(slot->sliding){uint16_t duration=slot->slide_duration?slot->slide_duration:1u;if(slot->slide_tick<duration)++slot->slide_tick;if(slot->slide_tick>=duration){slot->xq=(int32_t)o->x*256;slot->sliding=0u;}else slot->xq=(int32_t)o->x*256+(((int32_t)slot->slide_offset*256)*(int32_t)(duration-slot->slide_tick))/(int32_t)duration;if(o->kind==DMS_SCENE_KIND_ACTOR)ACTOR_setPosition(slot->handle,(int16_t)(slot->xq>>8),(int16_t)(slot->yq>>8));}
}
static void run_v2_event(const DmsSceneRuntimeEventDesc *e){
    DmsFxParams p={0};
    switch(e->op){
        case DMS_SCENE_OP_SHOW:if(e->target<g_v2->object_count){g_slots[e->target].enabled=1u;g_slots[e->target].ended=0u;spawn_v2(e->target);}break;
        case DMS_SCENE_OP_HIDE:hide_v2(e->target);break;
        case DMS_SCENE_OP_TYPEWRITER:start_typewriter(e->target,e->a);break;
        case DMS_SCENE_OP_SLIDE_IN:start_slide(e->target,e->a,e->b);break;
        case DMS_SCENE_OP_FX_START:
            p.intensity=(uint8_t)e->a;p.duration=(uint16_t)e->b;p.secondary=(uint8_t)e->c;p.palette_mask=(uint8_t)e->d;
            /* Scene Builder events do not currently expose a free RGB333 color
               field. Use the conventional bright default for flash/strobe so
               an authored FX_START can never silently become a black flash. */
            if(e->ref==DMS_FX_FLASH||e->ref==DMS_FX_PALETTE_STROBE)p.color=0x01FFu;
            (void)FX_start((DmsFxId)e->ref,&p);break;
        case DMS_SCENE_OP_MUSIC_PLAY:MUS_play(e->ref);break;
        case DMS_SCENE_OP_MUSIC_STOP:MUS_stop();break;
        case DMS_SCENE_OP_SFX_PLAY:SFX_play(e->ref);break;
        case DMS_SCENE_OP_MENU_ENABLE:g_menu_enabled=1u;menu_find_first();break;
        case DMS_SCENE_OP_WAIT_INPUT:g_waiting=1u;g_wait_mask=(uint8_t)(e->a?e->a:0xF0);break;
        case DMS_SCENE_OP_END:g_active=0u;break;
        case DMS_SCENE_OP_CAMERA_SET:SCENE_setCamera(e->a,e->b);break;
        case DMS_SCENE_OP_CAMERA_SPEED:SCENE_setCameraSpeed(e->a,e->b);break;
        case DMS_SCENE_OP_SCROLL_SET:g_scroll_ax=e->a;g_scroll_ay=e->b;g_scroll_bx=e->c;g_scroll_by=e->d;break;
        case DMS_SCENE_OP_VIDEO_MODE:if(e->a>=0&&e->a<=4){g_scene_mode=(uint8_t)e->a;BG_setVideoMode(g_scene_mode);FX_setMode(g_scene_mode);}break;
        case DMS_SCENE_OP_TRIGGER:g_trigger=e->ref;break;
        case DMS_SCENE_OP_SPAWN_FORMATION:spawn_formation(e);break;
        case DMS_SCENE_OP_CHECKPOINT:{int16_t x=e->a,y=e->b;if(e->target<g_v2->object_count){x=g_v2->objects[e->target].x;y=g_v2->objects[e->target].y;}else {DmsActor player=ACTOR_player();if(player!=DMS_SCENE_INVALID&&x==0&&y==0){x=ACTOR_x(player);y=ACTOR_y(player);}}GAMEPLAY_setCheckpoint(x,y,g_camera_x,g_camera_y);break;}
        case DMS_SCENE_OP_FLOW_EMIT:FLOW_emit(e->ref);break;
        default:break;
    }
}
static void update_v2(void){
    uint16_t i;int16_t ax,ay,bx,by;uint8_t advance_frame=1u;
    if(g_waiting){if(PAD_pressed(g_wait_mask))g_waiting=0u;else advance_frame=0u;}
    if(!g_waiting){while(g_event_cursor<g_v2->event_count&&g_v2->events[g_event_cursor].frame<=g_frame){run_v2_event(&g_v2->events[g_event_cursor++]);if(g_waiting){advance_frame=0u;break;}}}
    if(!g_active)return;
    g_camera_xq+=g_camera_vxq;g_camera_yq+=g_camera_vyq;g_camera_x=(int16_t)(g_camera_xq>>8);g_camera_y=(int16_t)(g_camera_yq>>8);
    ax=(int16_t)(g_scroll_ax+(((int32_t)g_camera_x*g_v2->parallax_a_x_q8)>>8));ay=(int16_t)(g_scroll_ay+(((int32_t)g_camera_y*g_v2->parallax_a_y_q8)>>8));
    bx=(int16_t)(g_scroll_bx+(((int32_t)g_camera_x*g_v2->parallax_b_x_q8)>>8));by=(int16_t)(g_scroll_by+(((int32_t)g_camera_y*g_v2->parallax_b_y_q8)>>8));
    BG_setScrollA(ax,ay);BG_setScrollB(bx,by);ACTOR_update();menu_update_v2();
    for(i=0u;i<g_v2->object_count&&i<DMS_SCENE_OBJECT_MAX;++i){
        const DmsSceneObjectResourceDesc *o=&g_v2->objects[i];SceneObjectSlot *slot=&g_slots[i];spawn_v2(i);if(!slot->spawned)continue;update_slot_effects(i);
        if((o->end_frame&&g_frame>=o->end_frame)||(o->end_trigger&&o->end_trigger==g_trigger)){end_v2(i);continue;}
        palette_update_v2(o,slot);
        if(o->kind==DMS_SCENE_KIND_ACTOR)continue;
        if(v2_text_object(o)){draw_v2_text(i);continue;}
        if(!slot->sliding)slot->xq+=o->velocity_x_q8;
        slot->yq+=o->velocity_y_q8;
        if(o->loop){if((slot->xq>>8)<o->despawn_left)slot->xq=(int32_t)o->spawn_x<<8;else if((slot->xq>>8)>o->despawn_right)slot->xq=(int32_t)o->spawn_x<<8;if((slot->yq>>8)<o->despawn_top||(slot->yq>>8)>o->despawn_bottom)slot->yq=(int32_t)o->spawn_y<<8;}
        {int16_t x=(int16_t)(slot->xq>>8),y=(int16_t)(slot->yq>>8);if(!o->screen_space){x=(int16_t)(x-(((int32_t)g_camera_x*o->parallax_x_q8)>>8));y=(int16_t)(y-(((int32_t)g_camera_y*o->parallax_y_q8)>>8));}SPR_setPosition(slot->handle,x,y);}
        animate_v2(o,slot);
    }
    FX_update(ax,ay,bx,by);g_trigger=0u;if(advance_frame)++g_frame;
}

uint8_t SCENE_start(uint16_t scene_resource_id){
    uint16_t i;const DmsSceneResourceDesc *scene=find_scene(scene_resource_id);
    if(!scene){
        /* Generic hard-visible failure instead of an unexplained black screen. */
        VDP_setMode(0u);
        cram16[0u]=0x01FFu;
        return 0u;
    }
    SCENE_stop();g_v2=scene;g_backend=SCENE_BACKEND_V2;g_active=1u;g_current=scene_resource_id;g_frame=0u;g_trigger=0u;g_event_cursor=0u;g_result=0u;g_font_base=DMS_SCENE_INVALID;g_waiting=0u;g_wait_mask=0xF0u;
    g_scene_mode=scene->video_mode;g_scroll_ax=scene->scroll_a_x;g_scroll_ay=scene->scroll_a_y;g_scroll_bx=scene->scroll_b_x;g_scroll_by=scene->scroll_b_y;
    g_camera_x=scene->camera_x;g_camera_y=scene->camera_y;g_camera_xq=(int32_t)g_camera_x<<8;g_camera_yq=(int32_t)g_camera_y<<8;g_camera_vxq=scene->camera_speed_x_q8;g_camera_vyq=scene->camera_speed_y_q8;g_menu_enabled=(uint8_t)(scene->flags&1u);g_menu_index=0u;
    for(i=0u;i<DMS_SCENE_OBJECT_MAX;++i){g_slots[i].active=g_slots[i].spawned=g_slots[i].ended=0u;g_slots[i].palette_pos=0u;g_slots[i].palette_tick=0u;g_slots[i].enabled=0u;g_slots[i].option_value=0;g_slots[i].text_count=0u;g_slots[i].typewriter_active=0u;g_slots[i].sliding=0u;g_slots[i].draw_x_prev=0;}
    for(i=0u;i<scene->object_count&&i<DMS_SCENE_OBJECT_MAX;++i){const DmsSceneObjectResourceDesc *o=&scene->objects[i];g_slots[i].enabled=o->visible;g_slots[i].option_value=o->option_value;if(o->option_type==DMS_SCENE_OPTION_LIVES)g_slots[i].option_value=(int16_t)GAMEPLAY_startingLives();else if(o->option_type&&g_slots[i].option_value<o->option_min)g_slots[i].option_value=o->option_min;}
    VDP_setMode(scene->video_mode);FX_init(scene->video_mode);if(scene->map_resource_id!=DMS_SCENE_INVALID)BG_loadMap(scene->map_resource_id);if(g_menu_enabled)menu_find_first();
    for(i=0u;i<scene->object_count&&i<DMS_SCENE_OBJECT_MAX;++i)spawn_v2(i);
    return 1u;
}
void SCENE_setCamera(int16_t x,int16_t y){g_camera_x=x;g_camera_y=y;g_camera_xq=(int32_t)x<<8;g_camera_yq=(int32_t)y<<8;}
int16_t SCENE_cameraX(void){return g_camera_x;}
int16_t SCENE_cameraY(void){return g_camera_y;}
void SCENE_setCameraSpeed(int16_t vx_q8,int16_t vy_q8){g_camera_vxq=vx_q8;g_camera_vyq=vy_q8;}
void SCENE_trigger(uint16_t id){g_trigger=id;}
uint16_t SCENE_current(void){return g_current;}
uint8_t SCENE_objectActive(uint16_t i){return (g_backend==SCENE_BACKEND_V2&&g_v2&&i<g_v2->object_count&&i<DMS_SCENE_OBJECT_MAX)?g_slots[i].active:0u;}

static void load_v1_visual(const DmsSceneDef *s){
    uint16_t i;const DmsSceneVisual *v=s->visual;volatile uint16_t *pa=vram16+(plane_a_base(s->video_mode)>>1);volatile uint16_t *pb=vram16+(BG_B_STD_BASE>>1);
    VDP_setMode(s->video_mode);if(v){for(i=0u;i<(uint16_t)(v->tile_count*32u);++i)vram8[i]=v->tiles[i];for(i=0u;i<v->palette_count;++i){uint16_t p=v->palette_ids[i]&7u,k;for(k=0u;k<16u;++k)cram16[p*16u+k]=v->palettes[i*16u+k];}for(i=0u;i<(uint16_t)(v->map_width*v->map_height);++i){if(v->map_a)pa[(i/v->map_width)*64u+(i%v->map_width)]=v->map_a[i];if(v->map_b)pb[(i/v->map_width)*64u+(i%v->map_width)]=v->map_b[i];}}
    g_font_base=768u;if(s->font_tiles)for(i=0u;i<SCENE_FONT_BYTES;++i)vram8[(uint32_t)g_font_base*32u+i]=s->font_tiles[i];if(s->font_palettes&&s->font_palette_ids)for(i=0u;i<s->font_palette_count;++i){uint16_t p=s->font_palette_ids[i]&7u,k;for(k=0u;k<16u;++k)cram16[p*16u+k]=s->font_palettes[i*16u+k];}
}
void SCENE_play(const DmsSceneDef *s){
    uint16_t i;if(!s)return;SCENE_stop();g_v1=s;g_backend=SCENE_BACKEND_V1;g_active=1u;g_frame=0u;g_result=0u;g_event_cursor=0u;g_menu_enabled=0u;g_menu_index=0u;load_v1_visual(s);FX_init(s->video_mode);
    for(i=0u;i<s->object_count&&i<DMS_SCENE_OBJECT_MAX;++i){g_v1_visible[i]=s->objects[i].visible;g_v1_count[i]=(uint8_t)text_length(s->objects[i].text);g_v1_x[i]=s->objects[i].x;}
}
static void run_v1_event(const DmsSceneEvent *e){
    if(e->target<DMS_SCENE_OBJECT_MAX&&g_v1&&e->target<g_v1->object_count){if(e->op==1u)g_v1_visible[e->target]=1u;else if(e->op==2u)g_v1_visible[e->target]=0u;else if(e->op==3u){g_v1_visible[e->target]=1u;g_v1_count[e->target]=0u;}else if(e->op==4u){g_v1_visible[e->target]=1u;g_v1_x[e->target]=(int16_t)(g_v1->objects[e->target].x+e->a);}}
    if(e->op==5u){DmsFxParams p={0};p.intensity=(uint8_t)e->a;p.duration=(uint16_t)e->b;p.secondary=(uint8_t)e->c;p.palette_mask=(uint8_t)e->d;FX_start((DmsFxId)e->ref,&p);}else if(e->op==6u)MUS_play(e->ref);else if(e->op==7u)MUS_stop();else if(e->op==8u)SFX_play(e->ref);else if(e->op==9u)g_menu_enabled=1u;else if(e->op==11u)g_active=0u;
}
static void update_v1(void){
    uint16_t i;uint8_t pad=PAD_read();while(g_event_cursor<g_v1->event_count&&g_v1->events[g_event_cursor].frame<=g_frame)run_v1_event(&g_v1->events[g_event_cursor++]);
    if(g_menu_enabled){uint8_t count=0u;for(i=0u;i<g_v1->object_count;++i)if(g_v1->objects[i].kind==2u)++count;if(count){if(PAD_pressed(DMS_BUTTON_UP)){g_menu_index=(g_menu_index==0u)?(uint8_t)(count-1u):(uint8_t)(g_menu_index-1u);SFX_play(g_v1->menu_move_sfx);}if(PAD_pressed(DMS_BUTTON_DOWN)){g_menu_index=(uint8_t)((g_menu_index+1u)%count);SFX_play(g_v1->menu_move_sfx);}if(PAD_pressed(DMS_BUTTON_A|DMS_BUTTON_START)){uint8_t q=0u;for(i=0u;i<g_v1->object_count;++i)if(g_v1->objects[i].kind==2u){if(q++==g_menu_index){g_result=g_v1->objects[i].action;break;}}SFX_play(g_v1->menu_validate_sfx);g_active=0u;}}}
    for(i=0u;i<g_v1->object_count&&i<DMS_SCENE_OBJECT_MAX;++i){const DmsSceneObjectDesc *o=&g_v1->objects[i];uint8_t p=o->palette;if(o->kind==2u&&g_menu_enabled){uint8_t q=0u,j;for(j=0u;j<i;++j)if(g_v1->objects[j].kind==2u)++q;if(q==g_menu_index)p=o->selected_palette;}clear_text_cells(g_v1->video_mode,(int16_t)(g_v1_x[i]*8),o->y*8u,63u);if(g_v1_visible[i])draw_text(g_v1->video_mode,o->text,(int16_t)(g_v1_x[i]*8),o->y*8u,p,1u,g_v1_count[i]);}
    (void)pad;FX_update(g_v1->scroll_a_x,g_v1->scroll_a_y,g_v1->scroll_b_x,g_v1->scroll_b_y);++g_frame;
}
void SCENE_update(void){if(!g_active)return;if(g_backend==SCENE_BACKEND_V2)update_v2();else if(g_backend==SCENE_BACKEND_V1)update_v1();}
uint8_t SCENE_isActive(void){return g_active;}
uint16_t SCENE_frame(void){return g_frame;}
uint16_t SCENE_result(void){return g_result;}
uint8_t SCENE_menuIndex(void){return g_menu_index;}

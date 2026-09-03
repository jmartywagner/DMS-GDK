#include <stdint.h>
#include "dms_collision.h"
#include "dms_resource_runtime.h"

static const DmsCollisionResourceDesc *g_coll;
static uint8_t g_event;
static uint16_t g_event_zone;

static const DmsCollisionResourceDesc *find_coll(uint16_t id){
    uint16_t i;
    for(i=0u;i<dms_collision_resource_count;++i) if(dms_collision_resources[i].resource_id==id) return &dms_collision_resources[i];
    return 0;
}
static uint8_t overlap(int16_t ax0,int16_t ay0,int16_t ax1,int16_t ay1,const DmsCollisionZoneDesc *z){
    return (uint8_t)(ax0<z->x1 && ax1>z->x0 && ay0<z->y1 && ay1>z->y0);
}
void dms_collision_runtime_init(void){g_coll=0;g_event=DMS_COLL_EVENT_NONE;g_event_zone=0u;}
void COLL_bind(uint16_t id){g_coll=find_coll(id);g_event=DMS_COLL_EVENT_NONE;g_event_zone=0u;}
void COLL_update(void){}
uint8_t COLL_event(void){return g_event;}
uint16_t COLL_eventZone(void){return g_event_zone;}
void COLL_clearEvent(void){g_event=DMS_COLL_EVENT_NONE;g_event_zone=0u;}
uint16_t dms_coll_world_width(void){return g_coll?g_coll->width_px:0u;}
uint16_t dms_coll_world_height(void){return g_coll?g_coll->height_px:0u;}
uint8_t dms_coll_solid_point(int16_t x,int16_t y,uint8_t target){
    uint16_t i;if(!g_coll)return 0u;
    for(i=0u;i<g_coll->zone_count;++i){
        const DmsCollisionZoneDesc *z=&g_coll->zones[i];
        if(!(z->flags&1u) || !(z->target_mask&target))continue;
        if(z->type!=DMS_COLL_SOLID && z->type!=DMS_COLL_ONE_WAY)continue;
        if(x>=z->x0 && x<z->x1 && y>=z->y0 && y<z->y1)return 1u;
    }
    return 0u;
}

/* Internal AABB resolver. x/y are actor pivot coordinates. */
void dms_coll_resolve_x(int16_t *x,int16_t y,int16_t prev_x,int16_t bx,int16_t by,int16_t bw,int16_t bh,int16_t vx,uint8_t target,uint8_t *wall){
    uint16_t i;
    int16_t ax0=(int16_t)(*x+bx), ay0=(int16_t)(y+by), ax1=(int16_t)(ax0+bw), ay1=(int16_t)(ay0+bh);
    int16_t p0=(int16_t)(prev_x+bx), p1=(int16_t)(p0+bw);
    if(!g_coll)return;
    for(i=0u;i<g_coll->zone_count;++i){
        const DmsCollisionZoneDesc *z=&g_coll->zones[i];
        if(!(z->flags&1u) || !(z->target_mask&target) || z->type!=DMS_COLL_SOLID)continue;
        if(!overlap(ax0,ay0,ax1,ay1,z))continue;
        if(vx>0 && p1<=z->x0){*x=(int16_t)(z->x0-bx-bw);if(wall)*wall=1u;}
        else if(vx<0 && p0>=z->x1){*x=(int16_t)(z->x1-bx);if(wall)*wall=1u;}
        else continue;
        ax0=(int16_t)(*x+bx);ax1=(int16_t)(ax0+bw);
    }
}
void dms_coll_resolve_y(int16_t x,int16_t *y,int16_t prev_y,int16_t bx,int16_t by,int16_t bw,int16_t bh,int16_t vy,uint8_t target,uint8_t *ground,uint8_t *ceiling){
    uint16_t i;
    int16_t ax0=(int16_t)(x+bx), ay0=(int16_t)(*y+by), ax1=(int16_t)(ax0+bw), ay1=(int16_t)(ay0+bh);
    int16_t py0=(int16_t)(prev_y+by), py1=(int16_t)(py0+bh);
    if(!g_coll)return;
    for(i=0u;i<g_coll->zone_count;++i){
        const DmsCollisionZoneDesc *z=&g_coll->zones[i];
        if(!(z->flags&1u) || !(z->target_mask&target))continue;
        if(z->type==DMS_COLL_ONE_WAY){
            if(vy>=0 && ax0<z->x1 && ax1>z->x0 && py1<=z->y0 && ay1>=z->y0){
                *y=(int16_t)(z->y0-by-bh);if(ground)*ground=1u;ay0=(int16_t)(*y+by);ay1=(int16_t)(ay0+bh);
            }
            continue;
        }
        if(z->type!=DMS_COLL_SOLID || !overlap(ax0,ay0,ax1,ay1,z))continue;
        if(vy>=0 && py1<=z->y0){*y=(int16_t)(z->y0-by-bh);if(ground)*ground=1u;}
        else if(vy<0 && py0>=z->y1){*y=(int16_t)(z->y1-by);if(ceiling)*ceiling=1u;}
        else continue;
        ay0=(int16_t)(*y+by);ay1=(int16_t)(ay0+bh);
    }
}
uint8_t dms_coll_events(int16_t x,int16_t y,int16_t bx,int16_t by,int16_t bw,int16_t bh,uint8_t target){
    uint16_t i;
    int16_t ax0=(int16_t)(x+bx),ay0=(int16_t)(y+by),ax1=(int16_t)(ax0+bw),ay1=(int16_t)(ay0+bh);
    if(!g_coll)return DMS_COLL_EVENT_NONE;
    for(i=0u;i<g_coll->zone_count;++i){
        const DmsCollisionZoneDesc *z=&g_coll->zones[i];
        uint8_t ev=DMS_COLL_EVENT_NONE;
        if(!(z->flags&1u) || !(z->target_mask&target) || !overlap(ax0,ay0,ax1,ay1,z))continue;
        if(z->type==DMS_COLL_DANGER)ev=DMS_COLL_EVENT_DANGER;
        else if(z->type==DMS_COLL_EXIT)ev=DMS_COLL_EVENT_EXIT;
        else if(z->type==DMS_COLL_TRIGGER)ev=DMS_COLL_EVENT_TRIGGER;
        else if(z->type==DMS_COLL_CHECKPOINT)ev=DMS_COLL_EVENT_CHECKPOINT;
        if(ev!=DMS_COLL_EVENT_NONE){g_event=ev;g_event_zone=z->id;return ev;}
    }
    return DMS_COLL_EVENT_NONE;
}

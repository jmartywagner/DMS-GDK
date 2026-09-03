#ifndef DMS_COLLISION_H
#define DMS_COLLISION_H
#include <stdint.h>
#define DMS_COLL_EVENT_NONE   0u
#define DMS_COLL_EVENT_DANGER 1u
#define DMS_COLL_EVENT_EXIT   2u
#define DMS_COLL_EVENT_TRIGGER 3u
#define DMS_COLL_EVENT_CHECKPOINT 4u
void COLL_bind(uint16_t dcoll_resource_id);
void COLL_update(void);
uint8_t COLL_event(void);
uint16_t COLL_eventZone(void);
void COLL_clearEvent(void);
#endif

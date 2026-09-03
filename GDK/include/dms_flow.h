#ifndef DMS_FLOW_H
#define DMS_FLOW_H

#include <stdint.h>

typedef void (*DmsFlowCallback)(void);
typedef uint8_t (*DmsFlowCondition)(void);

typedef struct {
    const char *name;
    uint8_t type;
    DmsFlowCallback enter;
    DmsFlowCallback update;
    DmsFlowCallback exit;
} DmsFlowStateDef;

typedef struct {
    uint16_t source;
    uint16_t destination;
    uint16_t event;
    uint16_t delay_frames;
    uint16_t priority;
    DmsFlowCondition condition;
    DmsFlowCallback on_begin;
} DmsFlowTransitionDef;

typedef struct {
    const DmsFlowStateDef *states;
    uint16_t state_count;
    const DmsFlowTransitionDef *transitions;
    uint16_t transition_count;
    uint16_t default_state;
} DmsFlowDefinition;

/* Defined by the generated game_flow.c of the project. */
extern const DmsFlowDefinition dms_flow_definition;

void FLOW_init(uint16_t initial_state);
void FLOW_update(void);
void FLOW_emit(uint16_t event_id);
void FLOW_force(uint16_t state_id);
uint16_t FLOW_current(void);
uint16_t FLOW_pendingDestination(void);
uint8_t FLOW_transitionPending(void);
const char *FLOW_stateName(void);

#endif

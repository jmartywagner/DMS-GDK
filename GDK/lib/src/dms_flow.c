#include <stdint.h>
#include "dms_flow.h"

#define FLOW_INVALID 0xFFFFu

/* Empty FLOW fallback is defined in dms_stubs.c. Keeping overrideable
   generated data in a separate translation unit prevents optimizer folding. */


static uint16_t g_state = FLOW_INVALID;
static uint16_t g_event = 0u;
static uint16_t g_pending_transition = FLOW_INVALID;
static uint16_t g_pending_frames = 0u;

static const DmsFlowStateDef *state_def(uint16_t id)
{
    if (id >= dms_flow_definition.state_count) return 0;
    return &dms_flow_definition.states[id];
}

static void enter_state(uint16_t id)
{
    const DmsFlowStateDef *s = state_def(id);
    if (!s) return;
    g_state = id;
    if (s->enter) s->enter();
}

static void leave_state(void)
{
    const DmsFlowStateDef *s = state_def(g_state);
    if (s && s->exit) s->exit();
}

static void commit_transition(uint16_t transition_index)
{
    const DmsFlowTransitionDef *t;
    if (transition_index >= dms_flow_definition.transition_count) return;
    t = &dms_flow_definition.transitions[transition_index];
    leave_state();
    g_pending_transition = FLOW_INVALID;
    g_pending_frames = 0u;
    g_event = 0u;
    enter_state(t->destination);
}

static uint16_t choose_transition(void)
{
    uint16_t i;
    uint16_t best = FLOW_INVALID;
    uint16_t best_priority = 0xFFFFu;
    for (i = 0u; i < dms_flow_definition.transition_count; ++i) {
        const DmsFlowTransitionDef *t = &dms_flow_definition.transitions[i];
        if (t->source != g_state) continue;
        if (t->event != 0u && t->event != g_event) continue;
        if (t->condition && !t->condition()) continue;
        if (best == FLOW_INVALID || t->priority < best_priority) {
            best = i;
            best_priority = t->priority;
        }
    }
    return best;
}

void FLOW_init(uint16_t initial_state)
{
    g_event = 0u;
    g_pending_transition = FLOW_INVALID;
    g_pending_frames = 0u;
    g_state = FLOW_INVALID;
    if (initial_state >= dms_flow_definition.state_count)
        initial_state = dms_flow_definition.default_state;
    enter_state(initial_state);
}

void FLOW_update(void)
{
    const DmsFlowStateDef *s;
    uint16_t transition_index;

    if (g_state == FLOW_INVALID) return;

    s = state_def(g_state);
    if (s && s->update) s->update();

    if (g_pending_transition != FLOW_INVALID) {
        if (g_pending_frames > 0u) {
            --g_pending_frames;
            if (g_pending_frames > 0u) return;
        }
        commit_transition(g_pending_transition);
        return;
    }

    transition_index = choose_transition();
    if (transition_index != FLOW_INVALID) {
        const DmsFlowTransitionDef *t = &dms_flow_definition.transitions[transition_index];
        g_pending_transition = transition_index;
        g_pending_frames = t->delay_frames;
        if (t->on_begin) t->on_begin();
        if (g_pending_frames == 0u) commit_transition(transition_index);
    }

    /* Events are edge-like by default. A state callback can emit again next frame. */
    g_event = 0u;
}

void FLOW_emit(uint16_t event_id)
{
    g_event = event_id;
}

void FLOW_force(uint16_t state_id)
{
    if (state_id >= dms_flow_definition.state_count) return;
    leave_state();
    g_pending_transition = FLOW_INVALID;
    g_pending_frames = 0u;
    g_event = 0u;
    enter_state(state_id);
}

uint16_t FLOW_current(void) { return g_state; }

uint16_t FLOW_pendingDestination(void)
{
    if (g_pending_transition == FLOW_INVALID || g_pending_transition >= dms_flow_definition.transition_count)
        return FLOW_INVALID;
    return dms_flow_definition.transitions[g_pending_transition].destination;
}

uint8_t FLOW_transitionPending(void)
{
    return (uint8_t)(g_pending_transition != FLOW_INVALID);
}

const char *FLOW_stateName(void)
{
    const DmsFlowStateDef *s = state_def(g_state);
    return s ? s->name : "";
}

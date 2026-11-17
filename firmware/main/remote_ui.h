#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "cJSON.h"
#include "recording_fsm.h"
enum {R_APP=10,R_REPLY,R_PICK,R_GLOBAL,R_AI,R_ENTER,R_ESCAPE,R_CANCEL,R_COPY,R_PASTE,R_REFRESH,R_SEND,R_EDIT,R_DISMISS,R_START,R_STOP,R_SELECT};
typedef struct {int action,index,offset;char id[40];} remote_request_t;
void remote_ui_init(void (*callback)(const remote_request_t *));
void remote_ui_status(const char *state,const char *detail,const char *session,bool global,bool mic);
void remote_ui_data(cJSON *reply);
void remote_ui_level(unsigned peak);

void remote_ui_recording(rec_state_t state);

void remote_ui_selection_done(int generation);

#pragma once
#include <stdbool.h>
/* One lifecycle shared by the UI and transport. All access is locked in main.c. */
typedef enum { REC_IDLE,REC_STARTING,REC_RECORDING,REC_STOPPING,REC_PROCESSING,REC_COMPLETED,REC_FAILED,REC_CANCELLED } rec_state_t;
typedef enum { REC_START,REC_STOP,REC_CANCEL,REC_ACK_RECORDING,REC_ACK_PROCESSING,REC_ACK_IDLE,REC_ERROR,REC_DISCONNECT } rec_event_t;
static inline bool rec_busy(rec_state_t s){return s>=REC_STARTING && s<=REC_PROCESSING;}
static inline rec_state_t rec_transition(rec_state_t s,rec_event_t e){
 switch(e){
 case REC_START:return rec_busy(s)?s:REC_STARTING;
 case REC_STOP:return s==REC_STARTING||s==REC_RECORDING?REC_STOPPING:s;
 case REC_CANCEL:return REC_CANCELLED;
 case REC_ACK_RECORDING:return s==REC_STARTING?REC_RECORDING:s;
 case REC_ACK_PROCESSING:return s==REC_STOPPING||s==REC_RECORDING?REC_PROCESSING:s;
 case REC_ACK_IDLE:return s==REC_PROCESSING?REC_COMPLETED:s;
 case REC_ERROR:return REC_FAILED;
 case REC_DISCONNECT:return rec_busy(s)?REC_FAILED:s;
 }return s;
}
static inline const char *rec_name(rec_state_t s){
 static const char *names[]={"IDLE","STARTING","RECORDING","STOPPING","PROCESSING","COMPLETED","FAILED","CANCELLED"};return names[s];
}

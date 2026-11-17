#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_random.h"
#include "nvs_flash.h"
#include "lwip/sockets.h"
#include "lwip/inet.h"
#include "lwip/tcp.h"
#include "cJSON.h"
#include "mbedtls/base64.h"
#include "mbedtls/md.h"
#include "bsp/smartring_plus.h"
#include "esp_lv_adapter.h"
#include "local_config.h"
#include "remote_ui.h"
#include "diagnostics.h"
#include "recording_fsm.h"
#define SMARTRING_SYMBOL_MICROPHONE "\xEF\x84\xB0"

static portMUX_TYPE lock=portMUX_INITIALIZER_UNLOCKED;
static char state[32]="disconnected", detail[180]="Connecting", session[96]="unknown";
static bool mic_ok=false;
static rec_state_t lifecycle=REC_IDLE;
static int64_t lifecycle_since;
static bool global_mode=true;
static uint64_t enter_nonce;
static uint32_t enter_counter=0;
static char bootstrap_session_id[40];
static QueueHandle_t commands, audio_frames;
typedef struct { uint32_t generation, index; uint8_t data[3840]; } pcm_frame_t;
static bool capturing=false;
static uint32_t capture_generation=0, capture_index=0;
static int audio_error=0;
static void capture_stop(void) {
    portENTER_CRITICAL(&lock);capturing=false;portEXIT_CRITICAL(&lock);
}
static uint32_t capture_begin(void) {
    capture_stop();xQueueReset(audio_frames);
    portENTER_CRITICAL(&lock);
    capture_generation++;capture_index=0;audio_error=0;capturing=true;
    uint32_t gen=capture_generation;portEXIT_CRITICAL(&lock);return gen;
}
static int capture_error(void) {
    portENTER_CRITICAL(&lock);int err=audio_error;portEXIT_CRITICAL(&lock);return err;
}
static void microphone_task(void *unused) {
    (void)unused; pcm_frame_t frame;
    while(1) {
        size_t count=0;
        int64_t read_start=esp_timer_get_time();
        esp_err_t err=i2s_channel_read(bsp_audio_get_rx(),frame.data,sizeof(frame.data),&count,100);
        diagnostics_audio(esp_timer_get_time()-read_start,err!=ESP_OK || count!=sizeof(frame.data));
        portENTER_CRITICAL(&lock);
        bool wanted=capturing;
        frame.generation=capture_generation;
        if(wanted)frame.index=capture_index++;
        if(wanted && (err!=ESP_OK || count!=sizeof(frame.data))) {audio_error=1;capturing=false;}
        portEXIT_CRITICAL(&lock);
        if(err!=ESP_OK || count!=sizeof(frame.data)) {vTaskDelay(pdMS_TO_TICKS(10));continue;}
        unsigned peak=0;const int16_t *samples=(const int16_t *)frame.data;
        for(unsigned i=0;i<sizeof(frame.data)/2;i+=2){int value=samples[i];unsigned magnitude=value<0?-value:value;if(magnitude>peak)peak=magnitude;}
        remote_ui_level(peak);
        // Always drain I2S, even while idle. Never replay stale DMA audio at start.
        if(wanted && xQueueSend(audio_frames,&frame,0)!=pdTRUE) {
            portENTER_CRITICAL(&lock);
            if(frame.generation==capture_generation){audio_error=2;capturing=false;}
            portEXIT_CRITICAL(&lock);
        }
    }
}
static const char *apps[]={"codex","chatgpt","grokbot"};
static void copy_utf8(char *out,size_t size,const char *text) {
    size_t n=strlen(text);if(n>=size)n=size-1;
    while(n && ((unsigned char)text[n]&0xC0)==0x80)n--;
    memcpy(out,text,n);out[n]=0;
}
static void set_state(const char *s,const char *d) {
    portENTER_CRITICAL(&lock);copy_utf8(state,sizeof(state),s);copy_utf8(detail,sizeof(detail),d);portEXIT_CRITICAL(&lock);
    remote_ui_status(s,d,session,global_mode,mic_ok);
}
static rec_state_t rec_get(void){portENTER_CRITICAL(&lock);rec_state_t s=lifecycle;portEXIT_CRITICAL(&lock);return s;}
static void rec_event(rec_event_t event){
    portENTER_CRITICAL(&lock);rec_state_t previous=lifecycle;lifecycle=rec_transition(lifecycle,event);
    if(lifecycle!=previous)lifecycle_since=esp_timer_get_time();
    rec_state_t next=lifecycle;portEXIT_CRITICAL(&lock);
    if(next!=previous)printf("RECORD_FSM %s -> %s event=%d\n",rec_name(previous),rec_name(next),event);
    remote_ui_recording(next);
}
static bool rec_timed_out(void){
    portENTER_CRITICAL(&lock);rec_state_t s=lifecycle;int64_t since=lifecycle_since;portEXIT_CRITICAL(&lock);
    return rec_busy(s) && esp_timer_get_time()-since>(s==REC_PROCESSING?50000000:s==REC_RECORDING?305000000:8000000);
}
static void ui_command(const remote_request_t *request) {
    if(request->action==R_START) {
        if(mic_ok && (!strcmp(state,"ready") || !strcmp(state,"idle") || !strcmp(state,"failed")))rec_event(REC_START);
        return;
    }
    if(request->action==R_STOP){rec_event(REC_STOP);return;}
    bool cancelling=request->action==R_CANCEL || request->action==R_ESCAPE;
    if(rec_busy(rec_get()) && !cancelling)return;
    if(cancelling){rec_event(REC_CANCEL);capture_stop();}
    if((cancelling?xQueueSendToFront(commands,request,0):xQueueSend(commands,request,0))!=pdTRUE){
        if(cancelling){xQueueReset(commands);xQueueSend(commands,request,0);}
        else {rec_event(REC_ERROR);set_state("failed","COMMAND_QUEUE_FULL");}
    }
}
static void wifi_event(void *arg,esp_event_base_t base,int32_t id,void *data) {
    (void)arg; (void)data;
    if(base==WIFI_EVENT && (id==WIFI_EVENT_STA_START || id==WIFI_EVENT_STA_DISCONNECTED)) esp_wifi_connect();
}
static int read_line(int fd,char *buf,size_t cap) {
    size_t n=0;
    while(n+1<cap) {
        int r=recv(fd,buf+n,1,0); if(r!=1) return -1;
        if(buf[n]=='\n') { buf[n]=0; return 0; } n++;
    }
    return -1;
}
static int send_all(int fd,const char *s,size_t n) {
    while(n) { int r=send(fd,s,n,0); if(r<=0) return -1; s+=r; n-=r; } return 0;
}
static const char *string(cJSON *o,const char *key) { cJSON *v=cJSON_GetObjectItem(o,key); return cJSON_IsString(v)?v->valuestring:""; }
// One TCP stream, with request IDs. PCM is pipelined; control waits only for its own reply.
static char response_buffer[8192];
static size_t response_count=0;
static uint32_t request_serial=0;
static int64_t last_response_us=0;
static int send_request(int fd,cJSON *o,uint32_t *id) {
    uint32_t serial=++request_serial;
    cJSON_AddNumberToObject(o,"v",1);cJSON_AddNumberToObject(o,"id",serial);
    char *text=cJSON_PrintUnformatted(o);cJSON_Delete(o);if(!text)return -1;
    size_t len=strlen(text);char *wire=malloc(len+2);
    if(!wire){cJSON_free(text);return -1;}
    memcpy(wire,text,len);wire[len]='\n';wire[len+1]=0;cJSON_free(text);
    int rc=send_all(fd,wire,len+1);free(wire);
    if(id)*id=serial;
    return rc;
}
// Returns 1 for one complete reply, 0 when a nonblocking poll has no complete reply.
static int receive_response(int fd,bool blocking,cJSON **out) {
    while(1) {
        char *end=memchr(response_buffer,'\n',response_count);
        if(end) {
            size_t consumed=(size_t)(end-response_buffer)+1;*end=0;
            cJSON *reply=cJSON_Parse(response_buffer);
            memmove(response_buffer,response_buffer+consumed,response_count-consumed);
            response_count-=consumed;
            if(!reply)return -1;
            *out=reply;return 1;
        }
        if(response_count>=sizeof(response_buffer)-1)return -1;
        int n=recv(fd,response_buffer+response_count,sizeof(response_buffer)-1-response_count,blocking?0:MSG_DONTWAIT);
        if(n>0){response_count+=(size_t)n;continue;}
        if(n==0)return -1;
        if(errno==EINTR)continue;
        if(!blocking && (errno==EAGAIN || errno==EWOULDBLOCK))return 0;
        return -1;
    }
}
static int apply_response(cJSON *reply) {
    int64_t apply_start=esp_timer_get_time();
    const char *st=string(reply,"state");if(!*st)return -1;
    last_response_us=esp_timer_get_time();
    if(!strcmp(st,"recording"))rec_event(REC_ACK_RECORDING);
    else if(!strcmp(st,"processing"))rec_event(REC_ACK_PROCESSING);
    else if(!strcmp(st,"idle") || !strcmp(st,"ready"))rec_event(REC_ACK_IDLE);
    else if(!strcmp(st,"failed"))rec_event(REC_ERROR);
    const char *warning=string(reply,"warning");
    set_state(st,*warning?warning:(*string(reply,"error")?string(reply,"error"):string(reply,"target")));
    if(!strcmp(warning,"FOCUS_CHANGED_RECORDING_CANCELLED"))set_state(st,"警告：焦点已变，已取消录音");
    portENTER_CRITICAL(&lock);copy_utf8(session,sizeof(session),string(reply,"session"));portEXIT_CRITICAL(&lock);
    if(*string(reply,"input_mode")) {
        portENTER_CRITICAL(&lock);global_mode=!strcmp(string(reply,"input_mode"),"global");portEXIT_CRITICAL(&lock);
    }
    remote_ui_data(reply);
    if(!strcmp(string(reply,"data_type"),"sessions")){
        cJSON *data=cJSON_GetObjectItem(reply,"data");cJSON *first=cJSON_GetArrayItem(cJSON_GetObjectItem(data,"items"),0);
        copy_utf8(bootstrap_session_id,sizeof(bootstrap_session_id),string(first,"id"));
    }
    remote_ui_status(st,detail,session,global_mode,mic_ok);
    diagnostics_wifi(esp_timer_get_time()-apply_start);
    return !strcmp(st,"failed")?1:0;
}
static int exchange(int fd,cJSON *o,char *rid,size_t rid_size) {
    uint32_t expected=0;
    if(send_request(fd,o,&expected))return -1;
    while(1) {
        cJSON *reply=NULL;
        if(receive_response(fd,true,&reply)!=1)return -1;
        cJSON *id=cJSON_GetObjectItem(reply,"id");
        bool matched=cJSON_IsNumber(id) && (uint32_t)id->valuedouble==expected;
        int rc=apply_response(reply);
        if(matched && rid)snprintf(rid,rid_size,"%s",string(reply,"record_id"));
        cJSON_Delete(reply);
        if(matched || rc!=0)return rc;
        // Earlier audio ACKs are consumed without mistaking them for stop/select replies.
    }
}
static int poll_audio_replies(int fd) {
    while(1) {
        cJSON *reply=NULL;int read=receive_response(fd,false,&reply);
        if(read<0)return -1;
        if(!read)break;
        int rc=apply_response(reply);cJSON_Delete(reply);
        if(rc)return rc;
    }
    return esp_timer_get_time()-last_response_us>2000000?-1:0;
}
static cJSON *op(const char *name) { cJSON *o=cJSON_CreateObject(); cJSON_AddStringToObject(o,"op",name);return o; }
static void remote_task(void *arg) {
    (void)arg;
    if(!strlen(REMOTE_SSID) || strlen(REMOTE_TOKEN)!=64) {
        set_state("setup_required","Configure WiFi / host / token"); vTaskDelete(NULL); return;
    }
    pcm_frame_t frame; unsigned char encoded[5121];
    while(1) {
        int fd=socket(AF_INET,SOCK_STREAM,IPPROTO_TCP);
        if(fd<0){vTaskDelay(pdMS_TO_TICKS(2000));continue;}
        int nodelay=1;setsockopt(fd,IPPROTO_TCP,TCP_NODELAY,&nodelay,sizeof(nodelay));
        struct timeval timeout={.tv_sec=2,.tv_usec=0};
        setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));
        setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));
        struct sockaddr_in addr={.sin_family=AF_INET,.sin_port=htons(REMOTE_PORT)};
        inet_pton(AF_INET,REMOTE_HOST,&addr.sin_addr);
        if(connect(fd,(struct sockaddr *)&addr,sizeof(addr))) {close(fd);vTaskDelay(pdMS_TO_TICKS(2000));continue;}
        response_count=0;request_serial=0;last_response_us=esp_timer_get_time();
        char line[512],rid[64]=""; int seq=0; uint32_t generation=0;
        if(read_line(fd,line,sizeof(line))) {close(fd);continue;}
        cJSON *hello=cJSON_Parse(line); if(!hello){close(fd);continue;}
        const char *challenge=string(hello,"challenge");
        unsigned char hash[32]; char proof[65];
        mbedtls_md_hmac(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256),(const unsigned char *)REMOTE_TOKEN,strlen(REMOTE_TOKEN),(const unsigned char *)challenge,strlen(challenge),hash);
        cJSON_Delete(hello);
        for(int i=0;i<32;i++)sprintf(proof+i*2,"%02x",hash[i]);
        cJSON *o=op("hello");cJSON_AddStringToObject(o,"proof",proof);
        if(exchange(fd,o,NULL,0)){close(fd);continue;}
        o=op("set_mode");cJSON_AddStringToObject(o,"mode",global_mode?"global":"ai");
        if(exchange(fd,o,NULL,0)){close(fd);continue;}
        rec_event(REC_DISCONNECT); xQueueReset(commands);
        // Real, bounded warm-up of the read-only Codex pages. It never activates
        // Windows, resumes a conversation, sends text, or claims input binding.
        bootstrap_session_id[0]=0;
        o=op("sessions");cJSON_AddStringToObject(o,"app","codex");cJSON_AddNumberToObject(o,"offset",0);
        if(exchange(fd,o,NULL,0)<0){close(fd);continue;}
        if(*bootstrap_session_id){o=op("reply");cJSON_AddStringToObject(o,"session_id",bootstrap_session_id);cJSON_AddNumberToObject(o,"offset",0);if(exchange(fd,o,NULL,0)<0){close(fd);continue;}}
        int64_t ping=0;
        while(1) {
            remote_request_t command={0}; int rc=0;
            if(rid[0]) {
                rc=poll_audio_replies(fd);
                if(rc<0)break;
                if(rc>0){capture_stop();rid[0]=0;}
            }
            if(xQueueReceive(commands,&command,0)==pdTRUE) {
                int action=command.action;
                capture_stop();rid[0]=0;
                if(action==R_SELECT){o=op("select");cJSON_AddStringToObject(o,"target",apps[command.index>=0 && command.index<3?command.index:0]);cJSON_AddStringToObject(o,"scope","application_current");}
                else if(action==R_APP){o=op("sessions");cJSON_AddStringToObject(o,"app",apps[command.index>=0 && command.index<3?command.index:0]);cJSON_AddNumberToObject(o,"offset",command.offset);}
                else if(action==R_REPLY || action==R_PICK){o=op(action==R_REPLY?"reply":"select_session");cJSON_AddStringToObject(o,"session_id",command.id);cJSON_AddNumberToObject(o,"offset",command.offset);}
                else if(action==R_GLOBAL || action==R_AI){o=op("set_mode");cJSON_AddStringToObject(o,"mode",action==R_GLOBAL?"global":"ai");}
                else if(action==R_ENTER)o=op(global_mode?"enter":"send");
                else if(action==R_ESCAPE){o=op("shortcut");cJSON_AddStringToObject(o,"key","escape");}
                else if(action==R_COPY || action==R_PASTE || action==R_REFRESH){o=op("action");cJSON_AddStringToObject(o,"key",action==R_COPY?"copy":action==R_PASTE?"paste":"refresh");}
                else if(action==R_SEND){o=op("send");cJSON_AddStringToObject(o,"scope",command.offset?"specific_session":"application_current");}
                else if(action==R_EDIT)o=op("edit");
                else if(action==R_DISMISS)o=op("dismiss_notification");
                else o=op("cancel");
                if(action==R_ENTER || action==R_SEND || action==R_COPY || action==R_PASTE || action==R_REFRESH){char unique[48];snprintf(unique,sizeof(unique),"%016llx-%lu",(unsigned long long)enter_nonce,(unsigned long)++enter_counter);cJSON_AddStringToObject(o,"action_id",unique);}
                rc=exchange(fd,o,NULL,0);
                if(action==R_SELECT)remote_ui_selection_done(command.offset);
            } else if(!rid[0] && (rec_get()==REC_STARTING || rec_get()==REC_STOPPING)) {
                o=op("start"); rc=exchange(fd,o,rid,sizeof(rid));
                seq=0;
                if(rc==0 && rid[0] && (rec_get()==REC_RECORDING || rec_get()==REC_STOPPING))generation=capture_begin();
                else if(rc>0)rec_event(REC_ERROR);
            } else if(rid[0]) {
                bool holding=rec_get()==REC_RECORDING;
                if(!holding)capture_stop();
                int fault=capture_error();
                if(fault) {
                    o=op("abort");cJSON_AddStringToObject(o,"error",fault==2?"MIC_BUFFER_OVERFLOW":"MIC_READ_FAILED");
                    rc=exchange(fd,o,NULL,0);rid[0]=0;
                } else if(xQueueReceive(audio_frames,&frame,pdMS_TO_TICKS(holding?100:0))==pdTRUE) {
                    if(frame.generation!=generation)continue;
                    if(frame.index!=(uint32_t)seq) {
                        capture_stop();o=op("abort");cJSON_AddStringToObject(o,"error","MIC_SAMPLE_GAP");
                        rc=exchange(fd,o,NULL,0);rid[0]=0;
                    } else {
                        size_t out=0;mbedtls_base64_encode(encoded,sizeof(encoded),&out,frame.data,sizeof(frame.data));encoded[out]=0;
                        o=op("audio");cJSON_AddStringToObject(o,"record_id",rid);cJSON_AddNumberToObject(o,"seq",seq++);
                        cJSON_AddStringToObject(o,"pcm",(char *)encoded);rc=send_request(fd,o,NULL);
                    }
                } else if(!holding) {
                    o=op("stop");cJSON_AddStringToObject(o,"record_id",rid);
                    rc=exchange(fd,o,NULL,0);rid[0]=0;
                } else {
                    capture_stop();o=op("abort");cJSON_AddStringToObject(o,"error","MIC_READ_FAILED");
                    rc=exchange(fd,o,NULL,0);rid[0]=0;
                }
            } else if(esp_timer_get_time()-ping>500000) {
                rc=exchange(fd,op("ping"),NULL,0);ping=esp_timer_get_time();
            }
            if(rc<0)break;
            if(rc>0){capture_stop();rid[0]=0;}
            if(rec_timed_out()){
                capture_stop();o=op("abort");cJSON_AddStringToObject(o,"error","DEVICE_AUDIO_ERROR");exchange(fd,o,NULL,0);rid[0]=0;rec_event(REC_ERROR);set_state("failed","RECORD_CONTROL_TIMEOUT");
            }
            vTaskDelay(pdMS_TO_TICKS(rid[0]?1:20));
        }
        capture_stop();close(fd);rec_event(REC_DISCONNECT);set_state("disconnected","Connection lost / recording stopped");
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}
void recording_fsm_selftest(void);
void app_main(void) {
    recording_fsm_selftest();
    enter_nonce=((uint64_t)esp_random()<<32)|esp_random();
    // No implicit NVS erasure; stop on incompatible storage.
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(bsp_display_init()); bsp_display_backlight_set(70);
    mic_ok=bsp_audio_init()==ESP_OK;
    if(mic_ok)esp_codec_dev_set_in_gain(bsp_audio_get_codec(),36.0f);
    commands=xQueueCreate(8,sizeof(remote_request_t)); configASSERT(commands);
    unsigned queue_capacity=16;
    audio_frames=xQueueCreate(queue_capacity,sizeof(pcm_frame_t));
    if(!audio_frames){queue_capacity=8;audio_frames=xQueueCreate(queue_capacity,sizeof(pcm_frame_t));}
    configASSERT(audio_frames);printf("AI_REMOTE audio queue: %u frames\n",queue_capacity);
    if(mic_ok) {
        BaseType_t created=xTaskCreate(microphone_task,"mic_capture",8192,NULL,6,NULL);
        if(created!=pdPASS){mic_ok=false;set_state("failed","MIC_TASK_CREATE_FAILED");}
    }
    ESP_ERROR_CHECK(esp_lv_adapter_lock(-1));diagnostics_init();remote_ui_init(ui_command);esp_lv_adapter_unlock();
    ESP_ERROR_CHECK(esp_netif_init());ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();wifi_init_config_t init=WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT,ESP_EVENT_ANY_ID,wifi_event,NULL));
    wifi_config_t wifi={0};snprintf((char *)wifi.sta.ssid,sizeof(wifi.sta.ssid),"%s",REMOTE_SSID);
    snprintf((char *)wifi.sta.password,sizeof(wifi.sta.password),"%s",REMOTE_PASSWORD);
    wifi.sta.threshold.authmode=WIFI_AUTH_WPA2_PSK;
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA,&wifi));
    if(strlen(REMOTE_SSID)){ESP_ERROR_CHECK(esp_wifi_start());ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));}
    xTaskCreate(remote_task,"ai_remote",16384,NULL,5,NULL);
}

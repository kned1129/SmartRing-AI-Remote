#include <stdio.h>
#include <string.h>
#include <inttypes.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "diagnostics.h"

/* Identical instrumentation on baseline and repaired builds. Never sends keys. */
static portMUX_TYPE lock=portMUX_INITIALIZER_UNLOCKED;
typedef struct {uint32_t frames,page_count,frame_max,page_max,audio_count,audio_max,audio_faults,wifi_count,wifi_max,touches,touch_max;uint64_t frame_us,page_us;} sample_t;
static sample_t s;
static int phase=-1;
static int64_t render_start,touch_read;
static lv_indev_read_cb_t original_read;
static lv_indev_state_t previous_touch;
static lv_point_t touch_point;
bool diagnostics_touch(lv_point_t *point){*point=touch_point;return previous_touch==LV_INDEV_STATE_PRESSED;}
static void display_event(lv_event_t *e){
 int64_t now=esp_timer_get_time();
 if(lv_event_get_code(e)==LV_EVENT_RENDER_START)render_start=now;
 if(lv_event_get_code(e)==LV_EVENT_RENDER_READY && render_start){
  uint32_t us=now-render_start;portENTER_CRITICAL(&lock);s.frames++;s.frame_us+=us;if(us>s.frame_max)s.frame_max=us;portEXIT_CRITICAL(&lock);render_start=0;
 }
}
static void touch_reader(lv_indev_t *in,lv_indev_data_t *data){
 int64_t start=esp_timer_get_time();original_read(in,data);
 if(data->state==LV_INDEV_STATE_PRESSED && previous_touch!=data->state)touch_read=start;
 previous_touch=data->state;
 touch_point=data->point;
}
static void touch_event(lv_event_t *e){
 if(lv_event_get_code(e)==LV_EVENT_PRESSED && touch_read){
  uint32_t us=esp_timer_get_time()-touch_read;touch_read=0;
  portENTER_CRITICAL(&lock);s.touches++;if(us>s.touch_max)s.touch_max=us;portEXIT_CRITICAL(&lock);
 }
}
int diagnostics_phase(void){return phase;}
void diagnostics_page(uint32_t us){portENTER_CRITICAL(&lock);s.page_count++;s.page_us+=us;if(us>s.page_max)s.page_max=us;portEXIT_CRITICAL(&lock);}
void diagnostics_audio(uint32_t us,int fault){portENTER_CRITICAL(&lock);s.audio_count++;s.audio_faults+=fault;if(us>s.audio_max)s.audio_max=us;portEXIT_CRITICAL(&lock);}
void diagnostics_wifi(uint32_t us){portENTER_CRITICAL(&lock);s.wifi_count++;if(us>s.wifi_max)s.wifi_max=us;portEXIT_CRITICAL(&lock);}
static uint64_t idle_runtime(void){
 TaskStatus_t tasks[40];UBaseType_t n=uxTaskGetSystemState(tasks,40,NULL);uint64_t idle=0;
 for(unsigned i=0;i<n;i++)if(strncmp(tasks[i].pcTaskName,"IDLE",4)==0)idle+=tasks[i].ulRunTimeCounter;
 return idle;
}
static void benchmark(void){
 static const char *names[]={"idle","status_churn","page_switch","mic_wave_wifi"};
 for(int p=0;p<4;p++){
  portENTER_CRITICAL(&lock);memset(&s,0,sizeof(s));portEXIT_CRITICAL(&lock);
  uint64_t idle=idle_runtime();int64_t start=esp_timer_get_time();phase=p;
  vTaskDelay(pdMS_TO_TICKS(p==0?5000:10000));
  phase=-1;int64_t elapsed=esp_timer_get_time()-start;uint64_t idle_end=idle_runtime();
  portENTER_CRITICAL(&lock);sample_t x=s;portEXIT_CRITICAL(&lock);
  double cpu=100.0*(1.0-(double)(idle_end-idle)/(elapsed*portNUM_PROCESSORS));
  printf("PERF {\"phase\":\"%s\",\"elapsed_us\":%lld,\"render_fps\":%.2f,\"frames\":%lu,\"render_mean_us\":%.1f,\"render_max_us\":%lu,\"page_count\":%lu,\"page_mean_us\":%.1f,\"page_max_us\":%lu,\"cpu_busy_pct\":%.2f,\"internal_free\":%u,\"internal_min\":%u,\"psram_free\":%u,\"largest_internal\":%u,\"audio_reads\":%lu,\"audio_read_max_us\":%lu,\"audio_faults\":%lu,\"wifi_responses\":%lu,\"wifi_apply_max_us\":%lu,\"touch_samples\":%lu,\"touch_read_to_dispatch_max_us\":%lu}\n",
   names[p],(long long)elapsed,x.frames*1000000.0/elapsed,(unsigned long)x.frames,x.frames?(double)x.frame_us/x.frames:0,(unsigned long)x.frame_max,(unsigned long)x.page_count,x.page_count?(double)x.page_us/x.page_count:0,(unsigned long)x.page_max,cpu,
   (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),(unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL),(unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM),(unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),(unsigned long)x.audio_count,(unsigned long)x.audio_max,(unsigned long)x.audio_faults,(unsigned long)x.wifi_count,(unsigned long)x.wifi_max,(unsigned long)x.touches,(unsigned long)x.touch_max);
 }
 printf("PERF_DONE\n");
}
static void console_task(void *unused){
 (void)unused;char command[40];unsigned n=0;
 while(1){int ch=getchar();if(ch==EOF){vTaskDelay(pdMS_TO_TICKS(20));continue;}
  if(ch=='\n'||ch=='\r'){command[n]=0;if(!strcmp(command,"benchmark"))benchmark();n=0;}
  else if(n<sizeof(command)-1)command[n++]=(char)ch;
 }
}
void diagnostics_init(void){
 lv_display_add_event_cb(lv_display_get_default(),display_event,LV_EVENT_ALL,NULL);
 lv_indev_t *in=lv_indev_get_next(NULL);
 if(in){original_read=lv_indev_get_read_cb(in);lv_indev_set_read_cb(in,touch_reader);lv_indev_add_event_cb(in,touch_event,LV_EVENT_PRESSED,NULL);}
 xTaskCreate(console_task,"diagnostics",6144,NULL,1,NULL);
}

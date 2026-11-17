#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <assert.h>
#include "freertos/FreeRTOS.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "lvgl.h"
#include "remote_ui.h"
#include "diagnostics.h"
LV_FONT_DECLARE(smartring_cjk_16);
#define MIC "\xEF\x84\xB0"
enum {HOME=1,APPS,SESSIONS,REPLY,RECORD,CONFIRM,QUICK,NOTICE,SETTINGS,CONTROL};
typedef struct {char id[40],title[164],time[24],verification[80];bool unread;} row_t;
typedef struct {
 char state[32],detail[180],session[100],typeless[32],error[128],notice[220];
 bool global,mic,unsupported,list_loaded,body_received,has_next,reply_next,reply_truncated;
 int count,offset,total,reply_offset,next_offset;row_t rows[3];
 char selected[40],title[164],body[1204],notice_id[40],notice_title[164],notice_digest[68];
 unsigned peak,version,data_version;int selection_generation,selection_confirmed;rec_state_t recording;bool basic_target;char target[24];
} model_t;
static model_t m={.state="disconnected",.global=true},v;
static portMUX_TYPE mux=portMUX_INITIALIZER_UNLOCKED;
static void (*dispatch)(const remote_request_t *);
static lv_obj_t *root,*badge,*clock_label,*record_title,*wave,*stop_button,*typeless_label,*input_label,*main_mic,*hint_label,*enter_button,*app_tabs[3];
static int page=HOME,application=0,return_page=HOME,reply_stack[96],reply_depth=0;
static unsigned rendered=0,levels[12];static unsigned level_at;
static bool needs_render=true,hold_mode=false;
static lv_area_t hold_area;
static bool hold_tracking,specific_confirmation,ui_event_test;
static unsigned data_rendered;
static rec_state_t shown_recording=REC_IDLE;
static int64_t record_started,record_ended;
static const char *names[]={"Codex","ChatGPT","GrokBot"};
static void textcopy(char *out,size_t size,const char *src){
 if(!src){src="";}
 size_t n=strlen(src);if(n>=size)n=size-1;
 while(n && ((unsigned char)src[n]&0xc0)==0x80)n--;
 memcpy(out,src,n);out[n]=0;
}
static const char *str(cJSON *o,const char *key){cJSON *x=cJSON_GetObjectItem(o,key);return cJSON_IsString(x)?x->valuestring:"";}
static int num(cJSON *o,const char *key){cJSON *value=cJSON_GetObjectItem(o,key);return cJSON_IsNumber(value)?value->valueint:0;}
static bool yes(cJSON *o,const char *key){return cJSON_IsTrue(cJSON_GetObjectItem(o,key));}
static void request(int action,int index,int offset,const char *id){
 remote_request_t r={.action=action,.index=index,.offset=offset};textcopy(r.id,sizeof(r.id),id);if(dispatch)dispatch(&r);
}
void remote_ui_status(const char *state,const char *detail,const char *session,bool global,bool mic){
 portENTER_CRITICAL(&mux);
 if(strcmp(m.state,state) && (!strcmp(state,"recording") || !strcmp(state,"ready"))){m.error[0]=0;m.notice[0]=0;}
 if(strcmp(m.state,state)||strcmp(m.detail,detail)||strcmp(m.session,session)||m.global!=global||m.mic!=mic)m.version++;
 textcopy(m.state,sizeof(m.state),state);textcopy(m.detail,sizeof(m.detail),detail);textcopy(m.session,sizeof(m.session),session);m.global=global;m.mic=mic;
 portEXIT_CRITICAL(&mux);
}
void remote_ui_selection_done(int generation){portENTER_CRITICAL(&mux);m.selection_confirmed=generation;m.version++;portEXIT_CRITICAL(&mux);}
void remote_ui_recording(rec_state_t state){portENTER_CRITICAL(&mux);m.recording=state;portEXIT_CRITICAL(&mux);}
void remote_ui_level(unsigned peak){portENTER_CRITICAL(&mux);m.peak=peak;portEXIT_CRITICAL(&mux);}
void remote_ui_data(cJSON *response){
 cJSON *data=cJSON_GetObjectItem(response,"data"),*notice=cJSON_GetObjectItem(response,"notification");
 portENTER_CRITICAL(&mux);
 m.basic_target=!strcmp(str(response,"target_scope"),"application_current");textcopy(m.target,sizeof(m.target),str(response,"target"));
 if(strcmp(m.typeless,str(response,"typeless_status"))){textcopy(m.typeless,sizeof(m.typeless),str(response,"typeless_status"));m.version++;}
 if(*str(response,"service_error")){textcopy(m.error,sizeof(m.error),str(response,"service_error"));m.version++;}
 if(*str(response,"service_notice")){textcopy(m.notice,sizeof(m.notice),str(response,"service_notice"));m.version++;}
 if(data){
  m.data_version++;
  m.error[0]=0;m.notice[0]=0;
  if(!strcmp(str(response,"data_type"),"sessions")){
   cJSON *items=cJSON_GetObjectItem(data,"items");m.count=cJSON_GetArraySize(items);if(m.count>3)m.count=3;m.list_loaded=true;
   m.offset=num(data,"offset");m.total=num(data,"total");m.has_next=yes(data,"has_next");m.unsupported=yes(data,"unsupported");
   for(int i=0;i<m.count;i++){cJSON *row=cJSON_GetArrayItem(items,i);textcopy(m.rows[i].id,sizeof(m.rows[i].id),str(row,"id"));textcopy(m.rows[i].title,sizeof(m.rows[i].title),str(row,"title"));textcopy(m.rows[i].time,sizeof(m.rows[i].time),str(row,"updated"));textcopy(m.rows[i].verification,sizeof(m.rows[i].verification),str(row,"verification"));m.rows[i].unread=yes(row,"unread");}
  }else if(!strcmp(str(response,"data_type"),"reply")){
   m.body_received=true;
   textcopy(m.selected,sizeof(m.selected),str(data,"id"));textcopy(m.title,sizeof(m.title),str(data,"title"));textcopy(m.body,sizeof(m.body),str(data,"body"));
   m.reply_offset=num(data,"offset");m.next_offset=num(data,"next_offset");m.reply_next=yes(data,"has_next");m.reply_truncated=yes(data,"truncated");
   if(*str(data,"input_error"))textcopy(m.notice,sizeof(m.notice),"只读会话：输入目标尚未验证");
  }m.version++;
 }
 if(notice && strcmp(m.notice_digest,str(notice,"digest"))){
  textcopy(m.notice_id,sizeof(m.notice_id),str(notice,"id"));textcopy(m.notice_title,sizeof(m.notice_title),str(notice,"title"));textcopy(m.notice_digest,sizeof(m.notice_digest),str(notice,"digest"));m.version++;m.data_version++;
 }
 portEXIT_CRITICAL(&mux);
 if(data && !strcmp(str(response,"data_type"),"sessions"))printf("REMOTE_DATA sessions=%d total=%d\n",cJSON_GetArraySize(cJSON_GetObjectItem(data,"items")),num(data,"total"));
 if(data && !strcmp(str(response,"data_type"),"reply"))printf("REMOTE_DATA reply_bytes=%u next=%d verified=%d\n",(unsigned)strlen(str(data,"body")),yes(data,"has_next"),yes(data,"verified"));
}
static void go(int p){page=p;needs_render=true;}
static void home(void){if(!v.global)request(R_GLOBAL,0,0,NULL);go(HOME);}
static void base_style(lv_obj_t *o){
 lv_obj_set_style_radius(o,18,0);lv_obj_set_style_bg_color(o,lv_color_hex(0x14213A),0);lv_obj_set_style_bg_grad_color(o,lv_color_hex(0x222544),0);lv_obj_set_style_bg_grad_dir(o,LV_GRAD_DIR_VER,0);
 lv_obj_set_style_bg_opa(o,LV_OPA_COVER,0);
 lv_obj_set_style_border_color(o,lv_color_hex(0x47649A),0);lv_obj_set_style_border_width(o,1,0);lv_obj_set_style_border_opa(o,75,0);lv_obj_set_style_pad_all(o,5,0);
}
static lv_obj_t *label(lv_obj_t *parent,const char *s,int x,int y,int w,lv_color_t color){
 lv_obj_t *l=lv_label_create(parent);lv_obj_set_pos(l,x,y);lv_obj_set_width(l,w);lv_label_set_long_mode(l,LV_LABEL_LONG_WRAP);lv_label_set_text(l,s);lv_obj_set_style_text_color(l,color,0);return l;
}
static lv_obj_t *center(const char *s,int y,int w,lv_color_t color){lv_obj_t *l=label(root,s,(360-w)/2,y,w,color);lv_obj_set_style_text_align(l,LV_TEXT_ALIGN_CENTER,0);return l;}
static lv_obj_t *button(lv_obj_t *parent,const char *s,int x,int y,int w,int h,lv_event_cb_t callback,int code,bool primary){
 lv_obj_t *b=lv_button_create(parent);lv_obj_set_pos(b,x,y);lv_obj_set_size(b,w,h);base_style(b);
 if(primary){lv_obj_set_style_bg_color(b,lv_color_hex(0x3868D9),0);lv_obj_set_style_bg_grad_color(b,lv_color_hex(0x8056D9),0);lv_obj_set_style_shadow_color(b,lv_color_hex(0x6064ED),0);lv_obj_set_style_shadow_width(b,6,0);lv_obj_set_style_shadow_opa(b,28,0);}
 lv_obj_set_style_bg_color(b,lv_color_hex(0x364E79),LV_STATE_PRESSED);lv_obj_set_style_shadow_width(b,4,LV_STATE_PRESSED);
 if(s){lv_obj_t *l=lv_label_create(b);lv_label_set_text(l,s);lv_obj_center(l);}
 if(callback){lv_obj_add_event_cb(b,callback,LV_EVENT_CLICKED,(void *)(intptr_t)code);}
 return b;
}
static const char *error_text(void){
 if(!strcmp(v.error,"BUSY"))return "正在录音或转写，请稍候";
 if(strstr(v.error,"SESSION") || strstr(v.error,"SWITCH"))return "无法验证电脑当前会话，请先在电脑端打开并确认";
 if(strstr(v.error,"FOCUS"))return "输入位置已变化，请在电脑重新选择输入框";
 if(strstr(v.error,"NO_ASSISTANT"))return "尚未读到可显示的 AI 回复";
 if(*v.error)return "读取失败：请刷新或检查电脑助手";
 return "";
}
static void navigate(lv_event_t *e){int p=(int)(intptr_t)lv_event_get_user_data(e);if(p==HOME)home();else go(p);}
static void swipe(lv_event_t *e){(void)e;lv_indev_t *in=lv_indev_active();if(in && lv_indev_get_gesture_dir(in)==LV_DIR_TOP && page==HOME)go(QUICK);}
static void mode_event(lv_event_t *e){int n=(int)(intptr_t)lv_event_get_user_data(e);request(n?R_AI:R_GLOBAL,0,0,NULL);go(n?APPS:HOME);}
static void app_event(lv_event_t *e){
 application=(int)(intptr_t)lv_event_get_user_data(e);
 portENTER_CRITICAL(&mux);int generation=++m.selection_generation;portEXIT_CRITICAL(&mux);
 request(R_SELECT,application,generation,NULL);go(CONTROL);
}
static void browse_event(lv_event_t *e){
 (void)e;portENTER_CRITICAL(&mux);m.count=0;m.error[0]=0;m.unsupported=false;m.list_loaded=false;portEXIT_CRITICAL(&mux);
 request(R_APP,application,0,NULL);go(SESSIONS);
}
static void session_event(lv_event_t *e){int n=(int)(intptr_t)lv_event_get_user_data(e);if(n>=v.count)return;reply_depth=0;reply_stack[0]=0;
 portENTER_CRITICAL(&mux);textcopy(m.selected,sizeof(m.selected),v.rows[n].id);textcopy(m.title,sizeof(m.title),v.rows[n].title);m.body[0]=0;m.error[0]=0;portEXIT_CRITICAL(&mux);
 request(R_PICK,application,0,v.rows[n].id);go(REPLY);}
static void list_page(lv_event_t *e){int delta=(int)(intptr_t)lv_event_get_user_data(e);int offset=v.offset+delta;if(offset<0)offset=0;request(R_APP,application,offset,NULL);}
static void reply_page(lv_event_t *e){int dir=(int)(intptr_t)lv_event_get_user_data(e);int offset=0;
 if(dir>0 && v.reply_next && reply_depth<95){reply_stack[++reply_depth]=v.next_offset;offset=v.next_offset;}
 else if(dir<0 && reply_depth>0)offset=reply_stack[--reply_depth];else return;
 request(R_REPLY,application,offset,v.selected);
}
static void confirm_history(lv_event_t *e){(void)e;specific_confirmation=true;go(CONFIRM);}
static void simple(lv_event_t *e){int action=(int)(intptr_t)lv_event_get_user_data(e);
 if(action==R_CANCEL){hold_tracking=false;request(action,0,0,NULL);if(v.global)home();else go(CONTROL);return;}
 if(action==R_REFRESH && page==REPLY){request(R_REPLY,application,v.reply_offset,v.selected);return;}
 if(action==R_REFRESH && page==SESSIONS){request(R_APP,application,v.offset,NULL);return;}
 request(action,application,action==R_SEND && specific_confirmation?1:0,v.selected);
}
static bool start_record(void){
 if(rec_busy(v.recording))return false;
 if(!v.global && (!v.basic_target || page==REPLY || page==SESSIONS)){
  portENTER_CRITICAL(&mux);int generation=++m.selection_generation;portEXIT_CRITICAL(&mux);
 request(R_SELECT,application,generation,NULL);go(CONTROL);return false;
 }
 if(!v.mic || !strcmp(v.state,"disconnected") || !strcmp(v.state,"locating"))return false;
 specific_confirmation=false;record_ended=0;memset(levels,0,sizeof(levels));level_at=0;return_page=page;
 record_started=esp_timer_get_time();request(R_START,0,0,NULL);go(RECORD);return true;
}
static void stop_record(void){hold_tracking=false;request(R_STOP,0,0,NULL);}
static void mic_event(lv_event_t *e){
 lv_event_code_t code=lv_event_get_code(e);
 if(hold_mode){
  if(code==LV_EVENT_PRESSED){lv_obj_get_coords(lv_event_get_target(e),&hold_area);hold_tracking=start_record();}
  else if(code==LV_EVENT_RELEASED || code==LV_EVENT_PRESS_LOST){
   lv_point_t p;bool down=diagnostics_touch(&p);if(ui_event_test)down=false;
   if(hold_tracking && (!down || (p.x<hold_area.x1 || p.x>hold_area.x2 || p.y<hold_area.y1 || p.y>hold_area.y2)))stop_record();
  }
 }else if(code==LV_EVENT_CLICKED){
  if(v.recording==REC_STARTING || v.recording==REC_RECORDING)stop_record();else start_record();
 }
}
static void stop_event(lv_event_t *e){(void)e;stop_record();}
static void hold_setting(lv_event_t *e){hold_mode=(bool)(intptr_t)lv_event_get_user_data(e);needs_render=true;}
static void notice_event(lv_event_t *e){int view=(int)(intptr_t)lv_event_get_user_data(e);request(R_DISMISS,0,0,NULL);
 if(view){application=0;reply_depth=0;request(R_REPLY,0,0,v.notice_id);go(REPLY);}else home();}
static lv_obj_t *mic_button(int x,int y,int size){
 lv_obj_t *b=button(root,MIC,x,y,size,size,NULL,0,true);lv_obj_set_style_radius(b,LV_RADIUS_CIRCLE,0);
 lv_obj_t *l=lv_obj_get_child(b,0);lv_obj_set_style_transform_scale_x(l,512,0);lv_obj_set_style_transform_scale_y(l,512,0);
 lv_obj_add_event_cb(b,mic_event,LV_EVENT_ALL,NULL);
 if(!v.mic || !strcmp(v.state,"disconnected")){lv_obj_add_state(b,LV_STATE_DISABLED);}
 return b;
}
static void draw_wave(lv_event_t *e){
 lv_layer_t *layer=lv_event_get_layer(e);lv_area_t box;lv_obj_get_coords(lv_event_get_target(e),&box);
 lv_draw_rect_dsc_t d;lv_draw_rect_dsc_init(&d);d.radius=3;d.bg_opa=LV_OPA_COVER;
 for(int i=0;i<12;i++){
  lv_area_t r={box.x1+i*16,box.y1,box.x1+i*16+6,box.y2};d.bg_color=lv_color_hex(0x17233B);lv_draw_rect(layer,&d,&r);
  unsigned h=levels[(level_at+i)%12]*60/32768;if(h){r.y1=r.y2-h+1;d.bg_color=lv_color_hex(0x8890F5);lv_draw_rect(layer,&d,&r);}
 }
}
static void render(void){
 int64_t render_started=esp_timer_get_time();
 lv_obj_clean(root);wave=NULL;clock_label=NULL;record_title=NULL;stop_button=NULL;typeless_label=NULL;input_label=NULL;main_mic=NULL;hint_label=NULL;enter_button=NULL;memset(app_tabs,0,sizeof(app_tabs));
 badge=center(!strcmp(v.state,"disconnected")?LV_SYMBOL_WIFI " 未连接":(!strcmp(v.state,"failed")?LV_SYMBOL_WARNING " 需要注意":LV_SYMBOL_WIFI " 电脑已连接"),24,220,lv_color_hex(!strcmp(v.state,"failed")?0xEDAECC:0xAABFDF));
 if(page!=HOME && page!=RECORD)button(root,LV_SYMBOL_LEFT,58,47,42,38,navigate,page==CONTROL?APPS:page==SESSIONS?CONTROL:page==REPLY?SESSIONS:page==CONFIRM && !v.global?CONTROL:HOME,false);
 if(page==HOME){
  center("全局语音",61,180,lv_color_hex(0xF3F5FF));main_mic=mic_button(124,101,112);
  center(hold_mode?"按住说话 · 松开结束":"点按开始 · 再点结束",221,260,lv_color_hex(0xD8E2FA));
  input_label=center("全局模式 · 输入框录音时验证",245,268,lv_color_hex(0xAABAD6));
  char line[90];snprintf(line,sizeof(line),"Typeless · %s",*v.typeless?v.typeless:"等待助手");typeless_label=center(line,270,250,lv_color_hex(0xAABAD6));
  button(root,"AI 会话",79,295,97,40,mode_event,1,false);enter_button=button(root,"↵ 回车",184,295,97,40,simple,R_ENTER,true);
 }else if(page==APPS){
  center("选择 AI 应用",62,210,lv_color_hex(0xF3F5FF));
  for(int i=0;i<3;i++){lv_obj_t *b=button(root,NULL,70,97+i*61,220,55,app_event,i,false);
   label(b,i==0?">_":(i==1?LV_SYMBOL_EDIT:LV_SYMBOL_LIST),12,12,28,lv_color_hex(0xA0BCFF));
   label(b,names[i],49,1,160,lv_color_hex(0xF4F6FF));label(b,i==0?"当前输入框 · 真实会话":"打开应用 · 当前输入框",49,25,162,lv_color_hex(0xADB9D4));}
 }else if(page==SESSIONS){
  center(names[application],62,170,lv_color_hex(0xF4F6FF));
  if(v.unsupported)center("暂不支持会话读取\n可使用应用当前输入框",137,220,lv_color_hex(0xB7C7E3));
  else if(*v.error)center(error_text(),125,230,lv_color_hex(0xEDBED3));
  else if(!v.count)center(v.list_loaded?"暂无可读取会话":"正在读取真实会话…",146,230,lv_color_hex(0xB7C7E3));
  else {lv_obj_t *list=lv_obj_create(root);lv_obj_set_pos(list,62,93);lv_obj_set_size(list,236,187);lv_obj_set_style_bg_opa(list,LV_OPA_TRANSP,0);lv_obj_set_style_border_width(list,0,0);lv_obj_set_style_pad_all(list,3,0);lv_obj_set_scroll_dir(list,LV_DIR_VER);
   for(int i=0;i<v.count;i++){lv_obj_t *b=button(list,NULL,0,i*66,222,61,session_event,i,false);lv_obj_t *l=label(b,v.rows[i].title,9,1,198,lv_color_hex(0xF3F5FF));lv_label_set_long_mode(l,LV_LABEL_LONG_DOT);lv_obj_set_height(l,22);
    char line[90];snprintf(line,sizeof(line),"%s  %s",v.rows[i].time,v.rows[i].unread?"新回复":"待验证");label(b,line,9,27,200,lv_color_hex(v.rows[i].unread?0xF0ACD2:0xAAB9D5));}}
  lv_obj_t *b=button(root,"上一组",76,291,98,40,list_page,-3,false);if(!v.offset)lv_obj_add_state(b,LV_STATE_DISABLED);
  b=button(root,"下一组",186,291,98,40,list_page,3,false);if(!v.has_next)lv_obj_add_state(b,LV_STATE_DISABLED);
 }else if(page==REPLY){
  lv_obj_t *title=label(root,*v.title?v.title:"读取回复…",110,62,188,lv_color_hex(0xEAF0FF));lv_label_set_long_mode(title,LV_LABEL_LONG_DOT);lv_obj_set_height(title,24);
  center("只读回复 · 语音需另选当前输入框",85,236,lv_color_hex(0xAABAD6));
  lv_obj_t *box=lv_obj_create(root);lv_obj_set_pos(box,57,111);lv_obj_set_size(box,246,134);base_style(box);lv_obj_set_scroll_dir(box,LV_DIR_VER);
  lv_obj_t *body=label(box,*v.error?error_text():(*v.body?v.body:"尚无可显示回复，请刷新"),7,5,218,lv_color_hex(0xDFE8FA));lv_obj_set_height(body,LV_SIZE_CONTENT);
  lv_obj_t *b=button(root,"上一页",77,250,98,35,reply_page,-1,false);if(!reply_depth)lv_obj_add_state(b,LV_STATE_DISABLED);
  b=button(root,v.reply_truncated?"继续 / 截断":"下一页",185,250,98,35,reply_page,1,false);if(!v.reply_next)lv_obj_add_state(b,LV_STATE_DISABLED);
  button(root,"刷新",67,293,70,38,simple,R_REFRESH,false);lv_obj_t *voice=button(root,"语音",145,293,70,38,NULL,0,true);lv_obj_add_event_cb(voice,mic_event,LV_EVENT_ALL,NULL);
  // A list selection is read-only; no invented desktop binding or send target.
  b=button(root,"发送",223,293,70,38,confirm_history,0,false);(void)b;
 }else if(page==RECORD){
  record_title=center(!strcmp(v.state,"processing")?"正在转写":"正在录音",63,220,lv_color_hex(0xF1F3FF));
  clock_label=center("00:00",100,200,lv_color_hex(0xC4D1FF));
  wave=lv_obj_create(root);lv_obj_remove_style_all(wave);lv_obj_set_pos(wave,84,148);lv_obj_set_size(wave,192,60);
  lv_obj_remove_flag(wave,LV_OBJ_FLAG_SCROLLABLE);lv_obj_set_style_bg_opa(wave,LV_OPA_COVER,0);lv_obj_set_style_bg_color(wave,lv_color_hex(0x080D19),0);
  lv_obj_add_event_cb(wave,draw_wave,LV_EVENT_DRAW_MAIN,NULL);
  lv_obj_add_flag(wave,LV_OBJ_FLAG_CLICKABLE);lv_obj_add_event_cb(wave,stop_event,LV_EVENT_CLICKED,NULL);
  center("轻点波形或停止结束",213,230,lv_color_hex(0xAABAD6));
  stop_button=button(root,LV_SYMBOL_STOP " 停止",118,235,124,53,stop_event,0,true);
  button(root,"取消",140,301,80,35,simple,R_CANCEL,false);
 }else if(page==CONFIRM){
  center("确认输入",64,180,lv_color_hex(0xEFF3FF));
  center(!strcmp(v.state,"disconnected")?"连接已断开，录音已停止":(!strcmp(v.state,"processing")?"Typeless 正在处理…":(!strcmp(v.state,"failed")?"录音未完成，请重新录音":"请在电脑端确认文字")),119,230,lv_color_hex(0xD7E1F8));
  center(specific_confirmation?"历史会话尚未绑定输入框\n请返回应用当前输入框操作":"未取得实际转写文本\n此处不会显示示例文字",174,228,lv_color_hex(0x9FAECC));
  if(*v.notice)center(v.notice,231,230,lv_color_hex(0xB6C9EF));else if(*v.error)center(error_text(),226,230,lv_color_hex(0xEDB7CF));
  else if(!strcmp(v.state,"failed"))center(strstr(v.detail,"FOCUS")||strstr(v.detail,"焦点")?"焦点已变化，录音已取消":(strstr(v.detail,"BUFFER")?"录音缓冲溢出，请重试":"录音异常，请检查电脑助手"),226,230,lv_color_hex(0xEDB7CF));
  button(root,"取消",67,286,70,44,simple,R_CANCEL,false);button(root,"编辑",145,286,70,44,simple,R_EDIT,false);
  lv_obj_t *b=button(root,"发送",223,286,70,44,simple,R_SEND,true);if((specific_confirmation || (!v.global && !v.basic_target)) || !strcmp(v.state,"processing")||!strcmp(v.state,"failed")||!strcmp(v.state,"disconnected"))lv_obj_add_state(b,LV_STATE_DISABLED);
 }else if(page==QUICK){
  center("快捷操作",63,190,lv_color_hex(0xF1F3FF));const char *keys[]={"↵ 回车","Esc","刷新","复制","粘贴","设置"};int acts[]={R_ENTER,R_ESCAPE,R_REFRESH,R_COPY,R_PASTE,0};
  for(int i=0;i<6;i++)button(root,keys[i],66+(i%2)*120,100+(i/2)*64,108,54,i==5?navigate:simple,i==5?SETTINGS:acts[i],false);
 }else if(page==NOTICE){
  center("Codex · 新回复",77,230,lv_color_hex(0xE6B9E6));lv_obj_t *nt=center(v.notice_title,126,228,lv_color_hex(0xEFF3FF));lv_label_set_long_mode(nt,LV_LABEL_LONG_DOT);lv_obj_set_height(nt,48);center("已检测到真实回复更新",202,230,lv_color_hex(0xAEBEDD));
  button(root,"查看",76,269,98,46,notice_event,1,true);button(root,"稍后",186,269,98,46,notice_event,0,false);
 }else if(page==CONTROL){
  center(names[application],60,190,lv_color_hex(0xF3F5FF));
  const char *tabs[]={"Codex","ChatGPT","GrokBot"};
  for(int i=0;i<3;i++)app_tabs[i]=button(root,tabs[i],62+i*80,91,76,36,app_event,i,i==application);
  main_mic=mic_button(135,137,90);
  input_label=center("正在验证当前输入框…",233,252,lv_color_hex(0xC3D2EF));
  center("当前对话操作 · 未绑定历史会话",259,265,lv_color_hex(0x9FAECC));
  enter_button=button(root,"↵ 回车",77,287,98,42,simple,R_ENTER,true);
  button(root,application==0?"会话阅读":"重选输入框",185,287,98,42,application==0?browse_event:app_event,application,false);
 }else if(page==SETTINGS){
  center("模式设置",64,190,lv_color_hex(0xF3F5FF));button(root,v.global?LV_SYMBOL_OK " 全局语音模式":"全局语音模式",70,103,220,49,mode_event,0,v.global);
  button(root,!v.global?LV_SYMBOL_OK " AI 会话模式":"AI 会话模式",70,164,220,49,mode_event,1,!v.global);
  center("录音方式",230,200,lv_color_hex(0xAABAD6));button(root,"按住",76,264,98,43,hold_setting,1,hold_mode);button(root,"点按",186,264,98,43,hold_setting,0,!hold_mode);
 }
 lv_obj_update_layout(root);diagnostics_page(esp_timer_get_time()-render_started);
}
/* Native LVGL event tests use a private dispatcher, never Windows commands. */
static unsigned tested_start,tested_stop,tested_cancel,tested_select;
static void test_dispatch(const remote_request_t *r){if(r->action==R_START)tested_start++;if(r->action==R_STOP)tested_stop++;if(r->action==R_CANCEL)tested_cancel++;if(r->action==R_SELECT)tested_select++;}
static void ui_recording_selftest(void){
 void (*saved)(const remote_request_t *)=dispatch;dispatch=test_dispatch;ui_event_test=true;
 strcpy(v.state,"ready");v.global=true;v.mic=true;v.recording=REC_IDLE;hold_mode=false;page=HOME;render();
 lv_obj_send_event(main_mic,LV_EVENT_CLICKED,NULL);assert(tested_start==1);
 v.recording=REC_STARTING;lv_obj_send_event(main_mic,LV_EVENT_CLICKED,NULL);assert(tested_stop==1);
 v.recording=REC_RECORDING;lv_obj_send_event(main_mic,LV_EVENT_CLICKED,NULL);assert(tested_stop==2);
 page=RECORD;render();lv_obj_send_event(wave,LV_EVENT_CLICKED,NULL);assert(tested_stop==3);lv_obj_send_event(stop_button,LV_EVENT_CLICKED,NULL);assert(tested_stop==4);
 hold_mode=true;v.recording=REC_IDLE;page=HOME;render();
 lv_obj_send_event(main_mic,LV_EVENT_PRESSED,NULL);assert(tested_start==2);
 lv_obj_send_event(main_mic,LV_EVENT_RELEASED,NULL);assert(tested_stop==5);
 v.recording=REC_IDLE;lv_obj_send_event(main_mic,LV_EVENT_PRESSED,NULL);lv_obj_send_event(main_mic,LV_EVENT_PRESS_LOST,NULL);assert(tested_start==3 && tested_stop==6);
 hold_mode=false;v.recording=REC_PROCESSING;lv_obj_send_event(main_mic,LV_EVENT_CLICKED,NULL);assert(tested_start==3);
 lv_obj_t *cancel=button(root,"cancel",0,0,40,40,simple,R_CANCEL,false);lv_obj_send_event(cancel,LV_EVENT_CLICKED,NULL);assert(tested_cancel==1);
 page=CONTROL;render();
 for(int i=0;i<3;i++){lv_obj_send_event(app_tabs[i],LV_EVENT_CLICKED,NULL);assert(application==i && page==CONTROL && tested_select==(unsigned)i+1);}
 lv_obj_send_event(app_tabs[0],LV_EVENT_CLICKED,NULL);assert(application==0 && tested_select==4);
 for(unsigned i=0;i<lv_obj_get_child_count(root);i++){
  lv_obj_t *child=lv_obj_get_child(root,i);
  if(lv_obj_check_type(child,&lv_button_class) && lv_obj_get_child_count(child)==1){lv_obj_t *l=lv_obj_get_child(child,0);if(lv_obj_check_type(l,&lv_label_class) && !strcmp(lv_label_get_text(l),LV_SYMBOL_LEFT)){lv_obj_send_event(child,LV_EVENT_CLICKED,NULL);break;}}
 }
 assert(page==APPS);
 hold_tracking=false;hold_mode=false;ui_event_test=false;dispatch=saved;
 portENTER_CRITICAL(&mux);m.selection_generation=m.selection_confirmed=0;v=m;portEXIT_CRITICAL(&mux);
 printf("UI_RECORDING_SELFTEST checks=15 PASS (private dispatcher, no Windows commands)\n");
}
static void update_status(void){
 const char *status=!strcmp(v.state,"disconnected")?LV_SYMBOL_WIFI " 未连接":(!strcmp(v.state,"failed")?LV_SYMBOL_WARNING " 需要注意":LV_SYMBOL_WIFI " 电脑已连接");
 if(badge && strcmp(lv_label_get_text(badge),status))lv_label_set_text(badge,status);
 if(typeless_label){char line[90];snprintf(line,sizeof(line),"Typeless · %s",*v.typeless?v.typeless:"等待助手");if(strcmp(lv_label_get_text(typeless_label),line))lv_label_set_text(typeless_label,line);}
 bool selecting=v.selection_generation!=v.selection_confirmed;
 if(input_label && page==CONTROL){const char *line=selecting?"正在验证新应用输入框…":!strcmp(v.state,"ready") || !strcmp(v.state,"idle") ?(v.basic_target?"当前输入框已验证":"请重新选择应用") : (!strcmp(v.state,"locating")?"正在定位输入框…":(strstr(v.detail,"FOCUS")?"焦点已变化，请重选":"输入框尚未就绪"));if(strcmp(lv_label_get_text(input_label),line))lv_label_set_text(input_label,line);}
 if(enter_button){bool blocked=rec_busy(v.recording) || selecting || !strcmp(v.state,"disconnected") || (!v.global && (!v.basic_target || (strcmp(v.state,"ready") && strcmp(v.state,"idle"))));if(blocked)lv_obj_add_state(enter_button,LV_STATE_DISABLED);else lv_obj_remove_state(enter_button,LV_STATE_DISABLED);}
 if(main_mic){bool blocked=selecting || !v.mic || !strcmp(v.state,"disconnected") || !strcmp(v.state,"locating") || (!v.global && (!v.basic_target || (strcmp(v.state,"ready") && strcmp(v.state,"idle"))));if(blocked)lv_obj_add_state(main_mic,LV_STATE_DISABLED);else lv_obj_remove_state(main_mic,LV_STATE_DISABLED);}
}
static void tick(lv_timer_t *t){
 (void)t;portENTER_CRITICAL(&mux);v=m;portEXIT_CRITICAL(&mux);
 static int previous_phase=-1;static unsigned bench_tick;static int64_t bench_time;
 int phase=diagnostics_phase();int64_t now=esp_timer_get_time();
 if(phase!=previous_phase){if(phase<0 && previous_phase>=0){page=HOME;needs_render=true;}previous_phase=phase;bench_tick=0;bench_time=now;if(phase>=0){page=phase==3?RECORD:HOME;needs_render=true;record_started=now;}}
 // Same 10 Hz diagnostic workload as baseline, independent of the UI timer rate.
 if(phase>=0 && now-bench_time>=100000){bench_time=now;++bench_tick;
  if(phase==1){snprintf(v.detail,sizeof(v.detail),"diagnostic-%u",bench_tick);v.version+=bench_tick;}
  if(phase==2 && bench_tick%5==0){page=page==HOME?APPS:HOME;needs_render=true;}
 }
 if(phase==3){strcpy(v.state,"recording");v.recording=REC_RECORDING;}
 static bool real_layout_tested;
 if(!real_layout_tested && phase<0 && page==HOME && !rec_busy(v.recording) && v.count>0 && v.body_received){
  page=SESSIONS;render();page=REPLY;render();page=CONTROL;render();page=HOME;needs_render=true;real_layout_tested=true;
  printf("UI_REAL_DATA_LAYOUT sessions=%d reply_bytes=%u basic_control=1\n",v.count,(unsigned)strlen(v.body));
 }
 if(hold_tracking){lv_point_t p;bool down=diagnostics_touch(&p);if(!down || (p.x<hold_area.x1 || p.x>hold_area.x2 || p.y<hold_area.y1 || p.y>hold_area.y2))stop_record();}
 if(page==RECORD && phase!=3){
  if(v.recording==REC_RECORDING && shown_recording==REC_STARTING)record_started=now;
  if(v.recording==REC_PROCESSING && !record_ended)record_ended=now;
  if(v.recording==REC_COMPLETED || v.recording==REC_FAILED || v.recording==REC_CANCELLED || !strcmp(v.state,"disconnected")){hold_tracking=false;go(CONFIRM);}
 }
 static char shown_notice[68];
 if(*v.notice_digest && strcmp(shown_notice,v.notice_digest) && page==HOME && !rec_busy(v.recording)){
  textcopy(shown_notice,sizeof(shown_notice),v.notice_digest);go(NOTICE);
 }
 // Status ACKs never destroy widgets. Only navigation or visible history changes rebuild.
 if(needs_render || (data_rendered!=v.data_version && (page==SESSIONS || page==REPLY || page==NOTICE))){
  needs_render=false;rendered=v.version;data_rendered=v.data_version;render();
 }
 if(page==CONFIRM && shown_recording!=v.recording){render();}
 update_status();
 if(page==RECORD){
  const char *title=phase==3?"音频绘制诊断":v.recording==REC_PROCESSING?"正在转写":v.recording==REC_STOPPING?"正在停止":v.recording==REC_STARTING?"正在启动":"正在录音";
  if(record_title && strcmp(lv_label_get_text(record_title),title))lv_label_set_text(record_title,title);
  if(clock_label){unsigned seconds=((record_ended?record_ended:now)-record_started)/1000000;char text[24];snprintf(text,sizeof(text),"%02u:%02u",seconds/60,seconds%60);if(strcmp(lv_label_get_text(clock_label),text))lv_label_set_text(clock_label,text);}
  static int64_t last_wave;
  if(v.recording==REC_RECORDING && now-last_wave>=40000){last_wave=now;levels[level_at++%12]=v.peak;if(wave)lv_obj_invalidate(wave);}
  if(stop_button){if(v.recording==REC_PROCESSING)lv_obj_add_state(stop_button,LV_STATE_DISABLED);else lv_obj_remove_state(stop_button,LV_STATE_DISABLED);}
 }
 shown_recording=v.recording;
}
void remote_ui_init(void (*callback)(const remote_request_t *)){
 dispatch=callback;root=lv_screen_active();lv_obj_remove_flag(root,LV_OBJ_FLAG_SCROLLABLE);lv_obj_set_style_bg_color(root,lv_color_hex(0x080D19),0);
 lv_obj_set_style_text_font(root,&smartring_cjk_16,0);lv_obj_set_style_text_color(root,lv_color_hex(0xE8EDFF),0);
 portENTER_CRITICAL(&mux);v=m;portEXIT_CRITICAL(&mux);
 // Render/layout smoke check on the real LVGL runtime, without fake session data
 // and without dispatching any keyboard, recording, or application commands.
 for(int p=HOME;p<=SETTINGS;p++){
  page=p;render();lv_obj_update_layout(root);
  printf("UI_RENDER_SMOKE page=%d root_children=%lu free_heap=%lu\n",p,(unsigned long)lv_obj_get_child_count(root),(unsigned long)heap_caps_get_free_size(MALLOC_CAP_8BIT));
 }
 ui_recording_selftest();page=CONTROL;render();printf("UI_BASIC_CONTROL_LAYOUT PASS\n");
 page=HOME;render();needs_render=false;lv_obj_add_event_cb(root,swipe,LV_EVENT_GESTURE,NULL);lv_timer_create(tick,20,NULL);
}

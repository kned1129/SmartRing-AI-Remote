# SmartRing 源码学习笔记

这份笔记选取本轮修改源码中的12段节选，帮助你从设备操作往回找代码。先认识关系，再慢慢学C和Python语法。

你在小屏点按钮 → LVGL界面把意图交给ESP32控制层 → 网络把JSON命令/声音送到Windows Agent → Agent验证输入框、把声音送入CABLE并点按Typeless快捷键 → Typeless把识别结果填进输入框 → Agent观察实际输入变化并回报小屏。读取Codex历史是另一条只读路径，不会自动证明输入框属于那个会话。

“状态”表示现在处在哪一步；“事件”表示刚发生什么；“队列”像按顺序等待处理的托盘。变量名看不懂时，先看它保存的是哪一种东西，不必一次掌握全部语法。

代码为真实文件节选，周边代码可能省略；行号以本次交付源码为准。

## 1. 录音状态是一张明确的路线图

文件：`firmware/main/recording_fsm.h`，第7行起。

```c
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
```

负责的功能：录音状态是一张明确的路线图。

你做什么会触发：点开始、点停止、取消、断线都会走这里。

先认识这些变量：s 是目前走到哪一步，e 是刚发生的动作。

为什么改、改后怎样：原来多个开关可能互相矛盾；现在每个事件只产生一个新状态。例如启动中已经点停止，迟到的开始确认不会把它改回录音。

## 2. 屏幕怎样理解点按与按住

文件：`firmware/main/remote_ui.c`，第151行起。

```c
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
```

负责的功能：屏幕怎样理解点按与按住。

你做什么会触发：点中央麦克风、按住后松开或移出按钮时触发。

先认识这些变量：code 是触摸事件，hold_mode 是用户选择的录音方式，v.recording 是状态快照。

为什么改、改后怎样：原点按只会开始。这次加了再次点击停止；按住模式根据真实触摸读数处理释放，避免切页删除控件后漏掉释放。

## 3. 界面请求怎样交给设备控制层

文件：`firmware/main/main.c`，第101行起。

```c
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
```

负责的功能：界面请求怎样交给设备控制层。

你做什么会触发：屏幕按钮发出开始、停止、取消请求时触发。

先认识这些变量：request->action 是请求种类，lifecycle 是唯一生命周期，commands 是待发网络命令队列。

为什么改、改后怎样：开始/停止先修改同一状态机；取消优先入队并立刻关闭采集。正常停止和取消各走各的事件，不能混用。

## 4. 麦克风一直读，录音时才把声音排队

文件：`firmware/main/main.c`，第59行起。

```c
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
```

负责的功能：麦克风一直读，录音时才把声音排队。

你做什么会触发：设备启动后持续执行；用户开始录音时 capturing 才允许入队。

先认识这些变量：frame 保存20ms声音，generation 是本轮录音编号，capture_index 是连续帧号。

为什么改、改后怎样：这次让采集开关只负责硬件队列，不再兼任录音生命周期。音频始终及时从硬件读走，避免开始时重放旧声音；UI性能统计可核对读取错误。

## 5. 状态变了，通常不用拆掉整页

文件：`firmware/main/remote_ui.c`，第321行起。

```c
 // Status ACKs never destroy widgets. Only navigation or visible history changes rebuild.
 if(needs_render || (data_rendered!=v.data_version && (page==SESSIONS || page==REPLY || page==NOTICE))){
  needs_render=false;rendered=v.version;data_rendered=v.data_version;render();
 }
 if(page==CONFIRM && shown_recording!=v.recording){render();}
 update_status();
 if(page==RECORD){
```

负责的功能：状态变了，通常不用拆掉整页。

你做什么会触发：电脑返回网络状态、Typeless状态和录音进度时触发。

先认识这些变量：needs_render 表示真的换页，data_version 表示历史内容变化，data_rendered 是已显示版本。

为什么改、改后怎样：旧版每个状态版本变化都可能 lv_obj_clean。现在普通状态只改 Label，只有换页或当前历史内容改变才重建。

## 6. 波形为什么改成一次绘制

文件：`firmware/main/remote_ui.c`，第174行起。

```c
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
```

负责的功能：波形为什么改成一次绘制。

你做什么会触发：录音期间音量更新时，LVGL要求重画这一小块。

先认识这些变量：levels 是最近12次峰值，level_at 是写到哪一格，r 是每根条的位置。

为什么改、改后怎样：原先12个Bar各自管理布局/样式；现在一个对象画12根条，减少对象和无效计算。真实测量更快，但这不等于ASR成功。

## 7. 三个应用直接在当前页切换

文件：`firmware/main/remote_ui.c`，第244行起。

```c
  const char *tabs[]={"Codex","ChatGPT","GrokBot"};
  for(int i=0;i<3;i++)app_tabs[i]=button(root,tabs[i],62+i*80,91,76,36,app_event,i,i==application);
  main_mic=mic_button(135,137,90);
  input_label=center("正在验证当前输入框…",233,252,lv_color_hex(0xC3D2EF));
  center("当前对话操作 · 未绑定历史会话",259,265,lv_color_hex(0x9FAECC));
  enter_button=button(root,"↵ 回车",77,287,98,42,simple,R_ENTER,true);
  button(root,application==0?"会话阅读":"重选输入框",185,287,98,42,application==0?browse_event:app_event,application,false);
```

负责的功能：三个应用直接在当前页切换。

你做什么会触发：在AI控制页点 Codex、ChatGPT 或 GrokBot。

先认识这些变量：application 是选中的应用，app_tabs 是三个按钮，app_event 发出切换请求。

为什么改、改后怎样：响应最新要求：不用先退全局主页。切换请求带代号，确认最新代号之前录音和回车不可点，避免旧回复误解锁。

## 8. ESP32发的是小命令，不是直接操控Windows

文件：`firmware/main/main.c`，第265行起。

```c
                if(action==R_SELECT){o=op("select");cJSON_AddStringToObject(o,"target",apps[command.index>=0 && command.index<3?command.index:0]);cJSON_AddStringToObject(o,"scope","application_current");}
                else if(action==R_APP){o=op("sessions");cJSON_AddStringToObject(o,"app",apps[command.index>=0 && command.index<3?command.index:0]);cJSON_AddNumberToObject(o,"offset",command.offset);}
                else if(action==R_REPLY || action==R_PICK){o=op(action==R_REPLY?"reply":"select_session");cJSON_AddStringToObject(o,"session_id",command.id);cJSON_AddNumberToObject(o,"offset",command.offset);}
                else if(action==R_GLOBAL || action==R_AI){o=op("set_mode");cJSON_AddStringToObject(o,"mode",action==R_GLOBAL?"global":"ai");}
                else if(action==R_ENTER)o=op(global_mode?"enter":"send");
                else if(action==R_ESCAPE){o=op("shortcut");cJSON_AddStringToObject(o,"key","escape");}
                else if(action==R_COPY || action==R_PASTE || action==R_REFRESH){o=op("action");cJSON_AddStringToObject(o,"key",action==R_COPY?"copy":action==R_PASTE?"paste":"refresh");}
                else if(action==R_SEND){o=op("send");cJSON_AddStringToObject(o,"scope",command.offset?"specific_session":"application_current");}
```

负责的功能：ESP32发的是小命令，不是直接操控Windows。

你做什么会触发：点应用、读取会话、按回车等操作时触发。

先认识这些变量：o 是JSON请求，target 指应用，scope说明只是当前输入框；exchange等待匹配的回复。

为什么改、改后怎样：恢复基础应用选择select，与只读sessions/reply分开。TCP负责送达，Windows Agent才负责找窗口和按键；ESP32不会猜测电脑已切成功。

## 9. Windows区分当前输入框与指定历史会话

文件：`windows-agent/smartring/core.py`，第87行起。

```python
            if scope not in ("application_current", "specific_session"):
                raise GuardError("BAD_TARGET_SCOPE")
            self.target = self.desktop.locate(app, hwnd)
            # A verified composer is sufficient for basic application operation.
            # History IDs must never be inferred from a window title or list click.
            if scope=="specific_session" and (not session_id or self.target.get("session_id")!=session_id):
                raise GuardError("SESSION_UNVERIFIED")
            if not self.desktop.check(self.target):
                raise GuardError("FOCUS_NOT_VERIFIED")
            self.target["scope"]=scope
            self.state = "ready"
        except Exception as e:
            return self.fail(str(e) if isinstance(e, GuardError) else "UIA_UNAVAILABLE")
```

负责的功能：Windows区分当前输入框与指定历史会话。

你做什么会触发：Windows Agent收到select请求后定位输入框时触发。

先认识这些变量：scope 是操作范围，session_id 是要精确绑定时才需要的会话编号，target 是观察到的窗口与控件证据。

为什么改、改后怎样：不知道历史ID不再禁止基本输入；若声称指定会话，就必须ID一致。ready只说明对应范围的验证通过。

## 10. 发消息前还要核对一次目标

文件：`windows-agent/smartring/features.py`，第21行起。

```python
            if op=='send':
                if ctl.input_mode=='ai':
                    if not ctl.target:
                        raise GuardError('SESSION_UNVERIFIED')
                    if (msg.get('scope')=='specific_session' or ctl.target.get('scope')!='application_current') and (not self.selected or ctl.target.get('session_id')!=self.selected['id']):
                        raise GuardError('SESSION_UNVERIFIED')
                    if not ctl.desktop.check(ctl.target):raise GuardError('FOCUS_LOST')
                    # Basic mode verifies the current composer; advanced mode verifies its ID too.
                    return self._key(msg,ctl.target,'enter')
                if not ctl.target or not ctl.desktop.check(ctl.target):raise GuardError('FOCUS_LOST')
                return self._key(msg,ctl.target,'enter')
            if op=='edit':return dict(**ctl.status(),service_notice='请在电脑端编辑文字')
```

负责的功能：发消息前还要核对一次目标。

你做什么会触发：用户明确点击发送按钮时触发。

先认识这些变量：ctl.target 是记录的输入框，selected 是只读历史选择，action_id用于防止重复按键。

为什么改、改后怎样：高级会话发送仍受ID约束，基础操作可以验证当前输入框。历史浏览不能偷偷接管基础发送，失焦则拒绝按键。Enter最终含义由应用决定。

## 11. Typeless开始与停止是两次点按

文件：`windows-agent/smartring/dictation.py`，第21行起。

```python
    def stop(self,target):
        if not self.active:return
        self.phase='STOPPING'
        self.desktop.typeless_event("stop",target)
        self.phase='PROCESSING'
    def poll(self,target):
        if not self.processing:return False
        if self.baseline is None:return False
        changed=self.desktop.input_stamp(target)!=self.baseline
        if changed:self.phase='COMPLETED'
        return changed
    def cancel(self):
        if self.active or self.processing:
```

负责的功能：Typeless开始与停止是两次点按。

你做什么会触发：用户停止录音，音频排空后触发stop；等待出字时触发poll。

先认识这些变量：phase 是Typeless控制阶段，baseline是原输入内容的指纹，不保存原文。

为什么改、改后怎样：用一个阶段字段替代两个布尔开关；STOPPING发第二次Right Alt，PROCESSING等待实际输入变化。发送了按键并不自动证明Typeless已正确出字。取消单独用Esc。

## 12. 计时器周期不等于真正的FPS

文件：`firmware/main/diagnostics.c`，第20行起。

```c
static void display_event(lv_event_t *e){
 int64_t now=esp_timer_get_time();
 if(lv_event_get_code(e)==LV_EVENT_RENDER_START)render_start=now;
 if(lv_event_get_code(e)==LV_EVENT_RENDER_READY && render_start){
  uint32_t us=now-render_start;portENTER_CRITICAL(&lock);s.frames++;s.frame_us+=us;if(us>s.frame_max)s.frame_max=us;portEXIT_CRITICAL(&lock);render_start=0;
 }
}
static void touch_reader(lv_indev_t *in,lv_indev_data_t *data){
 int64_t start=esp_timer_get_time();original_read(in,data);
```

负责的功能：计时器周期不等于真正的FPS。

你做什么会触发：LVGL真正开始渲染、结束渲染时触发。

先认识这些变量：render_start 是时间戳，frames 是完成次数，frame_us/frame_max统计总耗时和最大值。

为什么改、改后怎样：用实际渲染事件计数，而不是把100ms状态定时器当10FPS。诊断还记录音频读取、Wi-Fi处理、CPU与内存；没有触摸样本就必须写未测。

## 可以这样练习

先只读第2、3、8、9段：沿着“点按钮→命令→网络→电脑输入框”走一遍。再读第1和11段，理解“正在启动、正在录音、正在转写”为何不同。最后看第5、6、12段，理解性能优化为什么需要测量。

不必现在修改硬件引脚或字体。源码运行通过不等于手指操作和真人语音已经通过；请配合本次测试报告查看已测与待测项目。

import json
import time
import tkinter as tk
from pathlib import Path
from smartring.runtime import read_status
root=Path(__file__).resolve().parents[1]
w=tk.Tk(); w.title("SmartRing AI Remote V0.2 · 深色九页版"); w.geometry("560x580")
tk.Label(w,text="SmartRing AI Remote · 全局 / AI 双模式",font=("Microsoft YaHei",15)).pack(pady=15)
text=tk.StringVar(value="等待助手连接")
tk.Label(w,textvariable=text,justify="left",wraplength=480,font=("Microsoft YaHei",11)).pack(padx=20,anchor="w")
def update():
    try:
        s=read_status(root)
        age=time.time()-s.get("updated_at",0)
        text.set(f"设备：{s.get('peer','none')}\n模式：{s.get('input_mode','ai')}；目标：{s['target']} · {s['window']}\n会话：{s['session']}\n状态：{s['state']}  {s['error']} {s.get('warning','')}；本地麦克风：{s.get('local_microphone','off')}\n录音：{s.get('saved','')}\n助手心跳：{'正常' if age<4 else '已停止或不可用'}\n虚拟音频：{s.get('virtual_audio','off')}；Typeless由小屏启停；回车仅响应明确点击")
    except Exception: pass
    w.after(500,update)
tk.Label(w,text="文字预览（手动粘贴转写结果；只保存到本地）").pack(pady=10)
preview=tk.Text(w,height=9,wrap="word");preview.pack(fill="both",expand=True,padx=18)
notice=tk.StringVar()
def save():
    import uuid
    folder=root/"drafts";folder.mkdir(exist_ok=True)
    path=folder/(uuid.uuid4().hex+".txt")
    path.write_text(preview.get("1.0","end-1c"),encoding="utf8")
    notice.set("已保存："+path.name)
tk.Button(w,text="保存文字预览",command=save).pack(pady=8)
tk.Label(w,textvariable=notice).pack()
update();w.mainloop()

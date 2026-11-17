from pathlib import Path
import json,psutil,sys
r=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(r/"windows-agent"))
from smartring.runtime import runtime_dir
from smartring.resident import pause
pause(r)
p=runtime_dir(r)/"agent-process.json"
if not p.exists():p=r/"logs/agent-process.json"
if p.exists():
 info=json.loads(p.read_text())
 try:
  proc=psutil.Process(info["pid"])
  expected=str(r/"windows-agent/agent.py").lower()
  if abs(proc.create_time()-info["created"])<.01 and any(x.lower()==expected for x in proc.cmdline()):
   for child in proc.children(recursive=True):
    try:
     if any(x.lower()==str(r/"windows-agent/uia_worker.py").lower() for x in child.cmdline()):child.kill()
    except psutil.Error:pass
   proc.terminate();proc.wait(5);print("Project agent stopped")
  else: print("Process identity changed; nothing stopped")
 except psutil.NoSuchProcess: print("Agent already stopped")

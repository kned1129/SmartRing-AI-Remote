from pathlib import Path
import subprocess,sys,json,psutil
r=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(r/"windows-agent"))
from smartring.runtime import runtime_dir
infofile=runtime_dir(r)/"agent-process.json"
running=False
try:
 info=json.loads(infofile.read_text()); p=psutil.Process(info["pid"])
 running=abs(p.create_time()-info["created"])<.01 and any(x.lower()==str(r/"windows-agent/agent.py").lower() for x in p.cmdline())
except (OSError,ValueError,KeyError,psutil.Error): pass
if not running:
 subprocess.run([sys.executable,str(r/"tools/start_background.py")],check=True,creationflags=subprocess.CREATE_NO_WINDOW)
pythonw=Path(sys.executable).with_name("pythonw.exe")
subprocess.Popen([str(pythonw),str(r/"windows-agent/panel.py")],creationflags=subprocess.CREATE_NO_WINDOW)

"""Current-user logon watchdog; no UI activation, no firmware or device changes."""
import sys,time,json,logging
from pathlib import Path
from logging.handlers import RotatingFileHandler
import psutil
r=Path(__file__).resolve().parents[1];sys.path.insert(0,str(r/'windows-agent'))
from smartring.resident import instance,ensure_agent,paused
from smartring.runtime import runtime_dir
mutex=instance('supervisor',r)
if mutex is None:raise SystemExit(0)
folder=runtime_dir(r);folder.mkdir(parents=True,exist_ok=True)
(folder/'supervisor-process.json').write_text(json.dumps(dict(pid=psutil.Process().pid,created=psutil.Process().create_time())),encoding='utf8')
logging.basicConfig(handlers=[RotatingFileHandler(folder/'supervisor.log',maxBytes=250000,backupCount=2,encoding='utf8')],level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
logging.info('Supervisor started; logon and delayed-network recovery enabled')
while True:
    try:
        if not paused(r):
            info=ensure_agent(r)
            if not info['reused']:logging.info('Agent started pid=%s',info['pid'])
    except Exception as e:logging.warning('Retry in 5s: %s',type(e).__name__)
    time.sleep(5)

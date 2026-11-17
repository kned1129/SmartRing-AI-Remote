"""Per-project instance guards and an explicit stop-until-next-boot marker."""
import ctypes,hashlib,json,subprocess,sys,time
from pathlib import Path
import psutil
from .runtime import runtime_dir

def instance(name,root):
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_bool,ctypes.c_wchar_p];kernel.CreateMutexW.restype=ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    ident=hashlib.sha256(str(Path(root).resolve()).lower().encode()).hexdigest()[:12]
    handle=kernel.CreateMutexW(None,False,'Local\\SmartRing-'+name+'-'+ident)
    if not handle:raise ctypes.WinError(ctypes.get_last_error())
    if kernel.WaitForSingleObject(handle,0) not in (0,128):kernel.CloseHandle(handle);return None
    return handle  # OS releases ownership on process exit, including crashes.

def process(root,kind='agent'):
    path=runtime_dir(root)/(kind+'-process.json')
    expected=Path(root)/('windows-agent/agent.py' if kind=='agent' else 'tools/resident_supervisor.py')
    try:
        info=json.loads(path.read_text(encoding='utf8'));p=psutil.Process(info['pid'])
        if abs(p.create_time()-info['created'])<.01 and any(x.lower()==str(expected).lower() for x in p.cmdline()):return p
    except (OSError,ValueError,KeyError,psutil.Error):pass
    return None

def paused(root):
    try:return abs(float((runtime_dir(root)/'paused-this-boot').read_text())-psutil.boot_time())<1
    except (OSError,ValueError):return False

def pause(root):
    folder=runtime_dir(root);folder.mkdir(parents=True,exist_ok=True)
    (folder/'paused-this-boot').write_text(str(psutil.boot_time()),encoding='ascii')

def resume(root):
    (runtime_dir(root)/'paused-this-boot').unlink(missing_ok=True)

def _ensure_agent(root):
    root=Path(root);existing=process(root)
    if existing:return dict(agent_running=True,pid=existing.pid,reused=True)
    folder=runtime_dir(root);folder.mkdir(parents=True,exist_ok=True)
    with (folder/'resident-stdout.log').open('a',encoding='utf8') as out,(folder/'resident-stderr.log').open('a',encoding='utf8') as err:
        child=subprocess.Popen([str(root/'.venv/Scripts/python.exe'),str(root/'windows-agent/agent.py')],cwd=root,stdout=out,stderr=err,creationflags=subprocess.CREATE_NO_WINDOW)
    (folder/'agent-process.json').write_text(json.dumps(dict(pid=child.pid,created=psutil.Process(child.pid).create_time())),encoding='utf8')
    time.sleep(2)
    if child.poll() is not None:raise RuntimeError('Agent exited during startup; supervisor will retry')
    return dict(agent_running=True,pid=child.pid,reused=False)

def ensure_agent(root):
    guard=None
    for _ in range(60):
        guard=instance('launcher',root)
        if guard is not None:break
        time.sleep(.1)
    if guard is None:raise RuntimeError('Agent startup already in progress')
    try:return _ensure_agent(root)
    finally:
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.ReleaseMutex.argtypes=[ctypes.c_void_p];kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        kernel.ReleaseMutex(guard);kernel.CloseHandle(guard)

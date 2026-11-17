"""Bounded RPC around UI Automation. A stuck provider cannot hang the TCP agent."""
import ctypes,json,queue,subprocess,sys,threading,time
from pathlib import Path
from .core import GuardError
from .desktop import WindowsDesktop
from .runtime import runtime_dir

class SafeDesktop:
    def __init__(self,adapters,root,worker_path=None):
        self.adapters,self.root=adapters,Path(root)
        self.worker_path=Path(worker_path) if worker_path else self.root/"windows-agent/uia_worker.py"
        self.counter=0
        self.proc=None
        runtime=runtime_dir(self.root);runtime.mkdir(parents=True,exist_ok=True)
        self.stderr=(runtime/"uia-worker-stderr.log").open("a",encoding="utf8")
        self.user=ctypes.windll.user32
        self.user.GetForegroundWindow.restype=ctypes.c_void_p
        self.user.GetWindowThreadProcessId.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32)]
        self.user.GetWindowTextLengthW.argtypes=[ctypes.c_void_p]
        self.user.GetWindowTextW.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_int]
        self._spawn()
    quick_check=WindowsDesktop.quick_check
    def _spawn(self):
        self.responses=queue.Queue()
        self.proc=subprocess.Popen([sys.executable,str(self.worker_path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.stderr,text=True,encoding="utf8",bufsize=1,creationflags=subprocess.CREATE_NO_WINDOW)
        process,responses=self.proc,self.responses
        def reader():
            try:
                for line in process.stdout:
                    try:responses.put(json.loads(line))
                    except ValueError:continue
            finally:responses.put({"id":-1,"ok":False,"error":"UIA_WORKER_EXITED"})
        threading.Thread(target=reader,daemon=True).start()
        self.proc.stdin.write(json.dumps({"adapters":self.adapters})+"\n");self.proc.stdin.flush()
    def _kill(self):
        if self.proc:
            if self.proc.poll() is None:
                self.proc.kill()
            try:self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:pass
            for f in [self.proc.stdin,self.proc.stdout]:
                try:f.close()
                except OSError:pass
            self.proc=None
    def _rpc(self,op,timeout,**kwargs):
        if self.proc is None or self.proc.poll() is not None:
            self._kill();self._spawn()
        self.counter+=1;ident=self.counter
        try:
            self.proc.stdin.write(json.dumps(dict(id=ident,op=op,**kwargs))+"\n");self.proc.stdin.flush()
            deadline=time.monotonic()+timeout
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0:raise queue.Empty
                response=self.responses.get(timeout=remaining)
                if response.get("id")==0:continue
                if response.get("id")==-1:raise GuardError("UIA_WORKER_EXITED")
                if response.get("id")!=ident:raise GuardError("UIA_STALE_RESPONSE")
                if not response.get("ok"):raise GuardError(response.get("error","UIA_UNAVAILABLE"))
                return response.get("value")
        except queue.Empty:
            self._kill()
            raise GuardError("UIA_TIMEOUT")
        except (OSError,ValueError):
            self._kill()
            raise GuardError("UIA_UNAVAILABLE")
    def locate(self,app,hwnd=None):
        return self._rpc("locate",1.3,app=app,hwnd=hwnd)
    def capture_current(self):return self._rpc("capture_current",.5)
    def global_enter(self,target,combo):return self._rpc("global_enter",.5,target=target,combo=combo)
    def focused_key(self,target,key):return self._rpc('focused_key',.5,target=target,key=key)
    def check(self,target):
        if not self.quick_check(target):return False
        try:return bool(self._rpc("check",.2,target=target))
        except GuardError:return False
    def shortcut(self,key):return self._rpc("shortcut",.2,key=key)
    def input_stamp(self,target):return self._rpc("input_stamp",.3,target=target)
    def typeless_event(self,action,target=None):return self._rpc("typeless",.8,action=action,target=target)
    def close(self):
        self._kill();self.stderr.close()

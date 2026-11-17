import ctypes
import re
from .core import GuardError

class WindowsDesktop:
    def __init__(self, adapters):
        from pywinauto import Desktop
        from pywinauto.uia_defines import IUIA
        import psutil
        self.ui = Desktop(backend="uia")
        self.uia, self.psutil, self.adapters = IUIA(), psutil, adapters
        self.headings = {}
        self.user = ctypes.windll.user32
        self.user.GetForegroundWindow.restype = ctypes.c_void_p
        self.user.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        self.user.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
        self.user.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]

    def windows(self, app=None):
        import win32gui, win32process
        out=[]
        def visit(hwnd,unused):
            visible=bool(win32gui.IsWindowVisible(hwnd))
            if not visible and (not app or not self.adapters.get(app,{}).get("restore_existing_hidden",False)):return
            try:
                _,pid=win32process.GetWindowThreadProcessId(hwnd)
                process=self.psutil.Process(pid).name()
                title=win32gui.GetWindowText(hwnd)
                if app:
                    cfg=self.adapters.get(app)
                    if not cfg or process.lower() not in [x.lower() for x in cfg["processes"]]:return
                    if not re.search(cfg["window_title_regex"],title):return
                out.append(dict(hwnd=hwnd,pid=pid,process=process,title=title,hidden=not visible))
            except Exception:pass
        win32gui.EnumWindows(visit,None)
        return out

    def inputs(self, hwnd):
        w = self.ui.window(handle=hwnd).wrapper_object()
        return [c for c in w.descendants(control_type="Edit")
                if c.is_visible() and c.is_enabled() and not c.element_info.element.CurrentIsPassword]

    def session(self, app, hwnd):
        heading_id = self.adapters[app].get("session_heading_id")
        if not heading_id: return "unknown"
        headings = [x for x in self.ui.window(handle=hwnd).descendants()
                    if x.element_info.automation_id == heading_id and x.is_visible()]
        if len(headings) != 1: return "unknown"
        self.headings[hwnd] = headings[0].element_info.element
        return headings[0].element_info.name or "unknown"

    def locate(self, app, hwnd=None):
        cfg = self.adapters.get(app)
        if not cfg or not cfg["processes"]: raise GuardError("ADAPTER_NOT_CONFIGURED")
        candidates = self.windows(app)
        if hwnd is not None: candidates = [x for x in candidates if x["hwnd"] == hwnd]
        if not candidates: raise GuardError("WINDOW_NOT_FOUND")
        if cfg.get("restore_existing_hidden") and len(candidates)==1:
            import win32gui,win32con
            if candidates[0].get("hidden") or win32gui.IsIconic(candidates[0]["hwnd"]):
                # Only a remote selection restores an already-existing, uniquely matched window.
                win32gui.ShowWindow(candidates[0]["hwnd"],win32con.SW_RESTORE)
        eligible = []
        for t in candidates:
            controls = [c for c in self.inputs(t["hwnd"])
                        if re.fullmatch(cfg["input_name_regex"], c.element_info.name or "")
                        and (cfg.get("automation_id") is None or c.element_info.automation_id == cfg["automation_id"])]
            if len(controls) > 1: raise GuardError("AMBIGUOUS_INPUT")
            if len(controls) == 1: eligible.append((t, controls))
        if len(eligible) != 1:
            raise GuardError("INPUT_NOT_FOUND" if not eligible else "AMBIGUOUS_WINDOW")
        t, controls = eligible[0]
        c = controls[0]
        # SetFocus uses the actual UIA element, without screen coordinates.
        self.ui.window(handle=t["hwnd"]).set_focus()
        c.element_info.element.SetFocus()
        import time
        time.sleep(0.1)
        t.update(app=app, runtime_id=list(c.element_info.element.GetRuntimeId()),
                 input_name=c.element_info.name, automation_id=c.element_info.automation_id,
                 # Window titles are evidence; a configured alias is not an observed chat title.
                 session=self.session(app,t["hwnd"]), alias=cfg.get("session_label", ""))
        # Electron can replace its composer while a restored window gains focus.
        # Re-enumerate only the same uniquely matched input, then verify again.
        for attempt in range(3):
            if self.check(t):return t
            controls=[x for x in self.inputs(t['hwnd'])
                      if re.fullmatch(cfg['input_name_regex'],x.element_info.name or '')
                      and (cfg.get('automation_id') is None or x.element_info.automation_id==cfg['automation_id'])]
            if len(controls)!=1:raise GuardError('INPUT_NOT_FOUND' if not controls else 'AMBIGUOUS_INPUT')
            c=controls[0];self.ui.window(handle=t['hwnd']).set_focus();c.element_info.element.SetFocus()
            time.sleep(.08)
            t.update(runtime_id=list(c.element_info.element.GetRuntimeId()),input_name=c.element_info.name,
                     automation_id=c.element_info.automation_id)
        if not self.check(t):raise GuardError('FOCUS_NOT_VERIFIED')
        return t

    def quick_check(self, target):
        if self.user.GetForegroundWindow() != target["hwnd"]: return False
        pid = ctypes.c_uint32()
        self.user.GetWindowThreadProcessId(target["hwnd"], ctypes.byref(pid))
        if pid.value != target["pid"]: return False
        n = self.user.GetWindowTextLengthW(target["hwnd"])
        title = ctypes.create_unicode_buffer(n + 1)
        self.user.GetWindowTextW(target["hwnd"], title, n + 1)
        return title.value == target["title"]

    def capture_current(self):
        # Observation only: no SetFocus, ShowWindow, set_focus or application lookup.
        hwnd=self.user.GetForegroundWindow()
        if not hwnd:raise GuardError("FOCUS_UNAVAILABLE")
        pid=ctypes.c_uint32();self.user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        n=self.user.GetWindowTextLengthW(hwnd);title=ctypes.create_unicode_buffer(n+1)
        self.user.GetWindowTextW(hwnd,title,n+1)
        f=self.uia.iuia.GetFocusedElement()
        if not f or not f.CurrentHasKeyboardFocus or not f.CurrentIsEnabled or f.CurrentIsOffscreen:
            raise GuardError("FOCUS_UNAVAILABLE")
        if f.CurrentIsPassword:raise GuardError("PASSWORD_INPUT_BLOCKED")
        target=dict(app="global",hwnd=hwnd,pid=pid.value,title=title.value,session="unknown",
                    runtime_id=list(f.GetRuntimeId()),input_name="",automation_id=f.CurrentAutomationId,
                    allow_name_change=True,focus_pid=f.CurrentProcessId,focus_control_type=f.CurrentControlType)
        if not self.check(target):raise GuardError("FOCUS_CHANGED")
        return target

    def check(self, target):
        try:
            if not self.quick_check(target): return False
            if target["session"] != "unknown":
                heading = self.headings.get(target["hwnd"])
                if heading is None or heading.CurrentName != target["session"] or heading.CurrentIsOffscreen: return False
            f = self.uia.iuia.GetFocusedElement()
            if not f or not f.CurrentHasKeyboardFocus or not f.CurrentIsEnabled or f.CurrentIsOffscreen: return False
            if list(f.GetRuntimeId()) != target["runtime_id"]: return False
            if f.CurrentIsPassword:return False
            if target.get("focus_pid") and f.CurrentProcessId!=target["focus_pid"]:return False
            if not target.get("allow_name_change") and f.CurrentName != target["input_name"]: return False
            if f.CurrentAutomationId != target["automation_id"]: return False
            return True
        except Exception:
            return False

    def shortcut(self, key):
        from pywinauto.keyboard import send_keys
        send_keys({"escape":"{ESC}", "tab":"{TAB}"}[key])

    def input_stamp(self,target):
        import hashlib
        from pywinauto.uia_defines import get_elem_interface
        if not self.check(target):raise GuardError("FOCUS_NOT_VERIFIED")
        element=self.uia.iuia.GetFocusedElement()
        text=None
        try:text=get_elem_interface(element,"Value").CurrentValue
        except Exception:
            try:text=get_elem_interface(element,"Text").DocumentRange.GetText(131072)
            except Exception:raise GuardError("INPUT_TEXT_UNAVAILABLE")
        if text is None or len(text)>=131072:raise GuardError("INPUT_TEXT_UNAVAILABLE")
        return dict(sha256=hashlib.sha256(text.encode("utf8")).hexdigest(),length=len(text))

    @staticmethod
    def _tap_virtual_key(vk,extended=False):
        from pywinauto.keyboard import VirtualKeyAction,KEYEVENTF_EXTENDEDKEY
        from pywinauto import win32functions,win32structures
        # Submit down/up as one batch: never leave Right Alt held across a process timeout.
        inputs=VirtualKeyAction(vk).GetInput()
        if extended:
            for event in inputs:event.ki.dwFlags|=KEYEVENTF_EXTENDEDKEY
        count=win32functions.SendInput(len(inputs),ctypes.byref(inputs),ctypes.sizeof(win32structures.INPUT))
        if count!=len(inputs):raise GuardError("HOTKEY_SEND_FAILED")

    def typeless_event(self,action,target=None):
        if action=="start":
            try:stamp=self.input_stamp(target)
            except GuardError as e:
                if target.get("app")!="global" or str(e)!="INPUT_TEXT_UNAVAILABLE":raise
                stamp=None
            if not self.check(target):raise GuardError("FOCUS_NOT_VERIFIED")
            self._tap_virtual_key(0xA5,True)
            return stamp
        if action=="stop":
            if not self.check(target):raise GuardError("FOCUS_NOT_VERIFIED")
            self._tap_virtual_key(0xA5,True);return True
        if action=="cancel":
            self._tap_virtual_key(0x1B);return True
        raise GuardError("UNKNOWN_DICTATION_ACTION")

    def global_enter(self,target,combo):
        if not self.check(target):raise GuardError("FOCUS_CHANGED")
        if combo=="enter":self._tap_virtual_key(0x0D);return True
        if combo!="ctrl+enter":raise GuardError("KEY_NOT_ALLOWED")
        from pywinauto.keyboard import VirtualKeyAction
        from pywinauto import win32functions,win32structures
        events=[]
        for action in (VirtualKeyAction(0x11,up=False),VirtualKeyAction(0x0D),VirtualKeyAction(0x11,down=False)):
            events.extend(action.GetInput())
        batch=(win32structures.INPUT*len(events))(*events)
        count=win32functions.SendInput(len(batch),ctypes.byref(batch),ctypes.sizeof(win32structures.INPUT))
        if count!=len(batch):raise GuardError("KEY_RESULT_UNCERTAIN")
        return True

    def focused_key(self,target,key):
        if not self.check(target):raise GuardError('FOCUS_CHANGED')
        if key in ('enter','escape','refresh'):
            self._tap_virtual_key({'enter':0x0D,'escape':0x1B,'refresh':0x74}[key]);return True
        if key not in ('copy','paste'):raise GuardError('KEY_NOT_ALLOWED')
        from pywinauto.keyboard import VirtualKeyAction
        from pywinauto import win32functions,win32structures
        events=[]
        for action in (VirtualKeyAction(0x11,up=False),VirtualKeyAction(0x43 if key=='copy' else 0x56),VirtualKeyAction(0x11,down=False)):
            events.extend(action.GetInput())
        batch=(win32structures.INPUT*len(events))(*events)
        count=win32functions.SendInput(len(batch),ctypes.byref(batch),ctypes.sizeof(win32structures.INPUT))
        if count!=len(batch):raise GuardError('KEY_RESULT_UNCERTAIN')
        return True

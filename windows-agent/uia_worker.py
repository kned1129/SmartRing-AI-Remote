"""UIA calls live in a killable child process; one request at a time."""
import json,sys
from smartring.desktop import WindowsDesktop
from smartring.core import GuardError
first=json.loads(sys.stdin.readline())
d=WindowsDesktop(first["adapters"])
print(json.dumps({"id":0,"ok":True}),flush=True)
for line in sys.stdin:
    try:
        req=json.loads(line)
        op=req["op"]
        if op=="locate": value=d.locate(req["app"],req.get("hwnd"))
        elif op=="capture_current":value=d.capture_current()
        elif op=="global_enter":value=d.global_enter(req["target"],req["combo"])
        elif op=='focused_key':value=d.focused_key(req['target'],req['key'])
        elif op=="check": value=d.check(req["target"])
        elif op=="input_stamp": value=d.input_stamp(req["target"])
        elif op=="typeless": value=d.typeless_event(req["action"],req.get("target"))
        elif op=="shortcut": value=d.shortcut(req["key"])
        else: raise GuardError("UNKNOWN_UIA_OP")
        response={"id":req["id"],"ok":True,"value":value}
    except Exception as e:
        response={"id":req.get("id"),"ok":False,"error":str(e) if isinstance(e,GuardError) else "UIA_UNAVAILABLE"}
    print(json.dumps(response,ensure_ascii=True),flush=True)

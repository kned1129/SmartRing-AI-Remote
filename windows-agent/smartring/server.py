"""One authenticated TCP device; UIA stays on a single COM-initialized thread."""
import base64
import hashlib
import hmac
import json
import logging
import secrets
import socket
import time
from .core import Controller
from .runtime import write_status

MAX_LINE = 8192

def run(cfg, desktop, root):
    sink_factory=None
    sources=None
    if cfg.get("virtual_audio",{}).get("enabled",False):
        from .audio_sources import AudioSourceManager,LocalMicrophoneBridge
        local=cfg.get("local_microphone",{})
        factory=(lambda:LocalMicrophoneBridge(local.get("device_regex","Realtek"))) if local.get("enabled",False) else None
        sources=AudioSourceManager(factory)
        sink_factory=sources.make_remote
    dictation_factory=None
    if cfg.get("typeless",{}).get("automatic_trigger",False):
        from .dictation import TypelessToggle
        dictation_factory=lambda:TypelessToggle(desktop,cfg["typeless"].get("cancel_verified",False))
    ctl = Controller(desktop, root / "recordings", cfg["max_seconds"],sink_factory=sink_factory,dictation_factory=dictation_factory,
                     default_mode=cfg.get("default_mode","global"),strict_ai_sessions=cfg.get("strict_ai_sessions",False),enter_combo=cfg.get("enter_combo","enter"))
    from .sessions import CodexHistory
    from .features import DeviceFeatures
    history=CodexHistory(cfg.get('sessions',{}).get('codex_home'))
    features=DeviceFeatures(ctl,history)
    if cfg.get('sessions',{}).get('enabled',False):history.start(lambda:ctl.state in ('recording','processing'))
    typeless_check=[float('-inf'),'等待检查']
    def typeless_state():
        if not dictation_factory:return '未配置'
        if time.monotonic()-typeless_check[0]>2:
            import psutil
            try:typeless_check[1]='运行中' if any('typeless' in (p.info['name'] or '').lower() for p in psutil.process_iter(['name'])) else '未运行'
            except psutil.Error:typeless_check[1]='状态未知'
            typeless_check[0]=time.monotonic()
        return typeless_check[1]
    if len(cfg["token"]) != 64 or cfg["token"].startswith("REPLACE"):
        raise ValueError("Configure a random 64-character pairing token")
    previous = None
    peer = "none"
    last_publish = 0
    def publish():
        nonlocal previous, last_publish
        state = ctl.status()
        state['typeless_status']=typeless_state()
        state['transcript_available']=False
        if sources:
            sources.sync(ctl.state)
            state.update(sources.status())
        key = (state["state"],state["error"],state["target"],state["record_id"],state["input_mode"],state["warning"],state["enter_actions"],state["enter_injected"])
        changed = key != previous
        if changed or time.monotonic() - last_publish >= 1:
            state["peer"] = peer
            last_publish = time.monotonic()
            state["updated_at"] = time.time()
            try:
                write_status(root,state)
            except OSError:
                # Telemetry is optional. File sharing/permission failures must not kill recording.
                pass
            try:
                if changed: print(json.dumps(state,ensure_ascii=False),flush=True)
                if changed: logging.info("state=%s target=%s error=%s",ctl.state,state["target"],ctl.error)
            except OSError:
                pass
            previous = key
    with socket.socket() as listener:
        listener.bind((cfg["host"], cfg["port"]))
        listener.listen(1)
        listener.settimeout(.5)
        print(f"Listening {cfg['host']}:{cfg['port']} (preview only; Ctrl+C to stop)", flush=True)
        while True:
            publish()
            try:
                conn, addr = listener.accept()
            except socket.timeout:
                continue
            peer = addr[0]
            ctl.state, ctl.error, ctl.target = "idle", "", None
            ctl.warning=""
            try:
                with conn:
                    conn.settimeout(.1)
                    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    challenge = secrets.token_hex(32)
                    conn.sendall((json.dumps(dict(v=1, challenge=challenge))+"\n").encode())
                    authenticated, buffer, last = False, b"", time.monotonic()
                    while True:
                        try:
                            part = conn.recv(4096)
                            if not part: break
                            buffer += part
                        except socket.timeout:
                            pass
                        if len(buffer) > MAX_LINE and b"\n" not in buffer: raise ValueError("FRAME_TOO_LARGE")
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            if len(line) > MAX_LINE: raise ValueError("FRAME_TOO_LARGE")
                            msg = json.loads(line)
                            if not isinstance(msg, dict) or msg.get("v") != 1: raise ValueError("BAD_VERSION")
                            if not authenticated:
                                expected = hmac.new(cfg["token"].encode(), challenge.encode(), hashlib.sha256).hexdigest()
                                if msg.get("op") != "hello" or not hmac.compare_digest(str(msg.get("proof", "")), expected): raise ValueError("AUTH_FAILED")
                                authenticated = True
                                response = ctl.status()
                            else:
                                response = features.command(msg) if msg.get('op') in features.OPS else ctl.command(msg)
                                if msg.get('op')=='ping' and history.notification:
                                    response['notification']=dict(history.notification)
                            response['typeless_status']=typeless_state()
                            response['transcript_available']=False
                            publish()
                            response["peer"] = addr[0]
                            response["id"] = msg.get("id")
                            conn.sendall((json.dumps(response,ensure_ascii=False)+"\n").encode())
                            last = time.monotonic()
                        before = (ctl.state, ctl.error)
                        ctl.tick()
                        publish()
                        if time.monotonic() - last > 3: raise TimeoutError("PEER_TIMEOUT")
            except (Exception, KeyboardInterrupt) as e:
                logging.warning("connection ended: %s", type(e).__name__)
                if isinstance(e, KeyboardInterrupt): raise
            finally:
                ctl.finish("disconnected")
                ctl.target = None
                peer = "none"
                ctl.state = "disconnected"
                publish()
                print(json.dumps(ctl.status(),ensure_ascii=False), flush=True)

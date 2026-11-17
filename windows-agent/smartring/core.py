"""Guarded dictation; keyboard Enter only follows an explicit global-button action."""
import base64
import time
import uuid
import wave
from pathlib import Path
from . import __version__

class GuardError(Exception):
    pass

class Controller:
    def __init__(self, desktop, directory, max_seconds=300, clock=time.monotonic, sink_factory=None, dictation_factory=None, default_mode="ai", strict_ai_sessions=False, enter_combo="enter"):
        self.desktop, self.directory, self.clock = desktop, Path(directory), clock
        self.max_seconds = max_seconds
        self.sink_factory, self.sink = sink_factory, None
        self.dictation_factory,self.dictation = dictation_factory,None
        self.dictation_started=False
        self.processing_started=0
        self.last_stamp_check=float("-inf")
        self.target = None
        self.writer = None
        self.state, self.error, self.record_id = "disconnected", "", ""
        self.audio_bytes = 0
        self.last_focus_check = float("-inf")
        self.seq = 0
        self.started = self.last_audio = 0
        self.saved = ""
        self.input_mode=default_mode
        self.strict_ai_sessions=strict_ai_sessions
        self.enter_combo=enter_combo
        self.enter_actions=set()
        self.enter_injected=0
        self.warning=""

    def set_mode(self, mode):
        if mode not in ("global","ai"):return self.fail("BAD_MODE")
        if self.state in ("recording","processing"):return self.status_with_warning("BUSY")
        self.finish("cancelled")
        self.input_mode=mode;self.target=None;self.error="";self.warning=""
        self.state="ready" if mode=="global" else "idle"
        return self.status()

    def status_with_warning(self, warning):
        self.warning=warning
        return self.status()

    def global_enter(self, action_id):
        if self.input_mode!="global":return self.status_with_warning("GLOBAL_ONLY")
        if self.state in ("recording","processing"):return self.status_with_warning("BUSY")
        if not isinstance(action_id,str) or not 1<=len(action_id)<=80:return self.status_with_warning("ACTION_ID_REQUIRED")
        if action_id in self.enter_actions:return self.status_with_warning("DUPLICATE_KEY_IGNORED")
        if len(self.enter_actions)>=4096:return self.status_with_warning("KEY_LIMIT_RECONNECT_AGENT")
        # Mark before dispatch: even an uncertain RPC outcome must never be retried.
        self.enter_actions.add(action_id)
        try:
            target=self.desktop.capture_current()
            self.desktop.global_enter(target,self.enter_combo)
            self.enter_injected+=1
            self.warning="";self.error=""
        except Exception as e:
            self.warning=str(e) if isinstance(e,GuardError) else "KEY_RESULT_UNCERTAIN"
        return self.status()

    def status(self):
        return dict(v=1, agent_version=__version__, state=self.state, error=self.error,
                    target=self.target.get("app", "") if self.target else "",
                    window=self.target.get("title", "")[:160] if self.target else "",
                    session=self.target.get("session", "unknown") if self.target else "unknown",
                    target_scope=self.target.get("scope", "") if self.target else "",
                    record_id=self.record_id, saved=self.saved, mode="typeless_draft" if self.dictation_factory else "preview_only",
                    virtual_audio="routing" if self.sink else ("ready" if self.sink_factory else "off"), input_mode=self.input_mode, warning=self.warning, enter_actions=len(self.enter_actions), enter_injected=self.enter_injected)

    def fail(self, code):
        self.error = code
        self.finish("failed")
        self.state = "failed"
        return self.status()

    def select(self, app, hwnd=None, scope="application_current", session_id=None):
        if self.state in ("recording","processing"):return self.status_with_warning("BUSY")
        self.finish("cancelled")
        self.input_mode="ai";self.warning=""
        self.target = None
        self.state, self.error = "locating", ""
        try:
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
        return self.status()

    def start(self):
        if self.input_mode=="ai" and self.state=="idle" and self.target and self.desktop.check(self.target):
            self.state="ready"
        if self.input_mode=="global" and self.state in ("ready","idle","failed"):
            try:
                self.target=self.desktop.capture_current()
                self.state="ready";self.warning=""
            except Exception as e:return self.fail(str(e) if isinstance(e,GuardError) else "FOCUS_UNAVAILABLE")
        if self.state != "ready" or not self.target or not self.desktop.check(self.target):
            return self.fail("NOT_READY")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.record_id = uuid.uuid4().hex
        self.path = self.directory / (self.record_id + ".wav")
        self.writer = wave.open(str(self.path), "wb")
        self.writer.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        self.audio_bytes = 0
        self.last_focus_check = float("-inf")
        self.seq = 0
        if self.sink_factory:
            try:
                self.sink = self.sink_factory()
                self.sink.start()
            except Exception as e:
                return self.fail(str(e) if isinstance(e, GuardError) else "VIRTUAL_DEVICE_UNAVAILABLE")
        self.dictation_started=False
        if self.dictation_factory:
            try:
                self.dictation=self.dictation_factory()
                self.dictation.start(self.target)
                self.dictation_started=True
            except Exception as e:
                return self.fail(str(e) if isinstance(e,GuardError) else "TYPELESS_START_FAILED")
        # ESP32 starts PCM only after this start ACK. UIA/hotkey startup latency
        # is not missing microphone audio and must not reduce coverage_ratio.
        self.started = self.last_audio = self.clock()
        self.last_checkpoint = self.started
        self.state, self.error = "recording", ""
        return self.status()

    def audio(self, record_id, seq, encoded):
        if self.state == "failed" and self.error:return self.status()
        if self.state != "recording" or record_id != self.record_id:
            return self.fail("STALE_RECORDING")
        if seq != self.seq:
            return self.fail("AUDIO_SEQUENCE")
        try:
            pcm = base64.b64decode(encoded, validate=True)
        except Exception:
            return self.fail("BAD_PCM")
        if not pcm or len(pcm) > 3840 or len(pcm) % 4:
            return self.fail("BAD_PCM")
        self.tick()
        if self.state == "recording":
            self.writer.writeframesraw(pcm)
            self.audio_bytes += len(pcm)
            self.seq += 1
            if self.sink:
                try:self.sink.write(pcm)
                except Exception as e:
                    return self.fail(str(e) if isinstance(e,GuardError) else "VIRTUAL_AUDIO_ERROR")
            self.last_audio = self.clock()
            if self.last_audio - self.last_checkpoint >= 1:
                self.writer.writeframes(b"")
                self.last_checkpoint = self.last_audio
        return self.status()

    def tick(self, force=False):
        if self.sink and self.sink.error:
            return self.fail(self.sink.error)
        if self.state in ("ready", "recording", "processing") and not (self.input_mode=="global" and self.state=="ready"):
            try:
                quick = getattr(self.desktop, "quick_check", None)
                valid = self.target and (quick(self.target) if quick else True)
                if valid and (force or not quick or self.clock() - self.last_focus_check >= .1):
                    valid = self.desktop.check(self.target)
                    self.last_focus_check = self.clock()
            except Exception:
                valid = False
            if not valid:
                if self.input_mode=="global":self.warning="FOCUS_CHANGED_RECORDING_CANCELLED"
                return self.fail("FOCUS_LOST")
        if self.state == "processing":
            if self.clock()-self.processing_started>45:return self.fail("TRANSCRIPT_TIMEOUT")
            if self.clock()-self.last_stamp_check>=.15:
                self.last_stamp_check=self.clock()
                try:changed=self.dictation.poll(self.target)
                except Exception as e:return self.fail(str(e) if isinstance(e,GuardError) else "TRANSCRIPT_CHECK_FAILED")
                if changed:
                    self.dictation=None;self.state="idle"
                    self.target.pop("allow_name_change",None)
                    self._update_metadata(input_changed_after_typeless=True)
        if self.state == "recording":
            if self.clock() - self.started >= self.max_seconds:
                return self.fail("MAX_DURATION")
            if self.clock() - self.last_audio > 2:
                return self.fail("AUDIO_TIMEOUT")
        return self.status()

    def _update_metadata(self,**fields):
        if not self.saved:return
        try:
            import json
            path=Path(self.saved).with_suffix(".json")
            data=json.loads(path.read_text(encoding="utf8"));data.update(fields)
            path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf8")
        except (OSError,ValueError):pass

    def finish(self, reason="preview"):
        if reason!="preview" and self.dictation:
            current,self.dictation=self.dictation,None
            try:current.cancel()
            except Exception:self.error=self.error or "TYPELESS_CANCEL_FAILED"
        if not self.writer and reason=="failed" and self.state=="processing":
            self._update_metadata(post_record_error=self.error)
        if self.writer:
            elapsed = max(0, self.clock() - self.started)
            audio_seconds = self.audio_bytes / 192000
            coverage = audio_seconds / elapsed if elapsed else None
            if reason == "preview":
                if not self.audio_bytes:
                    reason, self.error = "incomplete", "EMPTY_RECORDING"
                elif elapsed >= 1 and coverage < .8:
                    reason, self.error = "incomplete", "AUDIO_DURATION_MISMATCH"
            routed = self.sink is not None
            if self.sink:
                sink,self.sink = self.sink,None
                try:sink.close(drain=reason=="preview")
                except Exception as e:
                    if not self.error:self.error=str(e) if isinstance(e,GuardError) else "VIRTUAL_AUDIO_ERROR"
                    reason="failed"
            if self.dictation:
                try:
                    if reason=="preview":
                        self.target["allow_name_change"]=True
                        self.dictation.stop(self.target)
                        self.processing_started=self.clock();self.last_stamp_check=float("-inf")
                    else:self.dictation.cancel();self.dictation=None
                except Exception as e:
                    self.error=str(e) if isinstance(e,GuardError) else "TYPELESS_STOP_FAILED"
                    try:self.dictation.cancel()
                    except Exception:pass
                    self.dictation=None;reason="failed"
            self.writer.close()
            self.writer = None
            self.saved = str(self.path)
            self.path.with_suffix(".json").write_text(__import__("json").dumps({
                "result": reason, "target": self.target, "frames": self.seq,
                "audio_bytes": self.audio_bytes, "audio_seconds": audio_seconds,
                "wall_seconds": elapsed, "coverage_ratio": coverage, "error": self.error,
                "virtual_audio_routed": routed, "transcribed": None if routed else False,
                "inserted": None if routed else False, "agent_triggered_typeless": self.dictation_started, "agent_sent_message": False}, ensure_ascii=False, indent=2), encoding="utf8")
            self._update_metadata(input_mode=self.input_mode,warning=self.warning)
        self.record_id = ""
        self.state = "failed" if reason in ("incomplete","failed") else ("processing" if self.dictation and self.dictation.processing else "idle")
        return self.status()

    def command(self, msg):
        op = msg.get("op")
        if op == "set_mode":return self.set_mode(msg.get("mode"))
        if op == "enter":return self.global_enter(msg.get("action_id"))
        if op == "select": return self.select(msg.get("target"), msg.get("hwnd"),msg.get("scope","application_current"),msg.get("session_id"))
        if op == "start": return self.start()
        if op == "audio": return self.audio(msg.get("record_id"), msg.get("seq"), msg.get("pcm", ""))
        if op == "stop":
            if self.state=="failed" and self.error:return self.status()
            if self.state != "recording" or msg.get("record_id") != self.record_id:
                return self.fail("STALE_RECORDING")
            self.tick(force=True)
            if self.state != "recording": return self.status()
            return self.finish()
        if op == "abort":
            code = msg.get("error")
            if code not in ("MIC_BUFFER_OVERFLOW", "MIC_READ_FAILED", "MIC_SAMPLE_GAP"):
                code = "DEVICE_AUDIO_ERROR"
            return self.fail(code)
        if op in ("cancel", "back"): return self.finish("cancelled")
        if op == "shortcut":
            # Deliberately restricted to navigation keys; never Return/Enter/paste/Typeless.
            if self.state in ("recording","processing"):
                if msg.get("key")=="escape":return self.finish("cancelled")
                return self.status_with_warning("BUSY")
            if self.input_mode=="global":
                if msg.get("key") not in ("escape","tab"):return self.fail("KEY_NOT_ALLOWED")
                try:self.target=self.desktop.capture_current()
                except Exception:return self.fail("FOCUS_UNAVAILABLE")
            if not self.target or not self.desktop.check(self.target): return self.fail("NOT_READY")
            key = msg.get("key")
            if key not in ("escape", "tab"): return self.fail("KEY_NOT_ALLOWED")
            self.desktop.shortcut(key)
            return self.tick()
        if op == "ping": return self.tick()
        return self.fail("UNKNOWN_OP")

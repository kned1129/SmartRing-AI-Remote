import base64
import tempfile
import unittest
import wave
from pathlib import Path
from smartring.core import Controller, GuardError

PCM=base64.b64encode(bytes(3840)).decode()
class Desktop:
    valid=True
    def locate(self, app, hwnd=None):
        if app == "missing": raise GuardError("WINDOW_NOT_FOUND")
        return dict(app=app, hwnd=1, pid=2, title="test", session="unknown")
    def check(self, target): return self.valid
    def shortcut(self, key): self.key=key

class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now=0
        self.d=Desktop()
        self.c=Controller(self.d,self.tmp.name,max_seconds=5,clock=lambda:self.now)
        self.addCleanup(lambda:self.c.finish())
    def ready(self): self.c.select("codex")
    def record(self): self.ready(); self.c.start(); return self.c.record_id
    def test_start_without_focus(self): self.assertEqual(self.c.start()["error"],"NOT_READY")
    def test_missing(self): self.assertEqual(self.c.select("missing")["state"],"failed")
    def test_focus_must_be_confirmed(self):
        self.d.valid=False
        self.assertEqual(self.c.select("codex")["error"],"FOCUS_NOT_VERIFIED")
    def test_focus_lost_before_start(self):
        self.ready(); self.d.valid=False
        self.assertEqual(self.c.start()["error"],"NOT_READY")
    def test_focus_lost_during_record(self):
        self.record(); self.d.valid=False
        self.assertEqual(self.c.tick()["error"],"FOCUS_LOST")
        self.assertIsNone(self.c.writer)
    def test_focus_lost_before_stop(self):
        rid=self.record(); self.d.valid=False
        self.assertEqual(self.c.command(dict(op="stop",record_id=rid))["error"],"FOCUS_LOST")
    def test_sequence_gap(self):
        rid=self.record(); self.assertEqual(self.c.audio(rid,1,PCM)["error"],"AUDIO_SEQUENCE")
    def test_old_recording(self):
        self.record(); self.assertEqual(self.c.audio("old",0,PCM)["error"],"STALE_RECORDING")
    def test_corrupt_pcm(self):
        rid=self.record(); self.assertEqual(self.c.audio(rid,0,"%%%")["error"],"BAD_PCM")
    def test_oversized_pcm(self):
        rid=self.record(); self.assertEqual(self.c.audio(rid,0,base64.b64encode(bytes(3844)).decode())["error"],"BAD_PCM")
    def test_misaligned_pcm(self):
        rid=self.record(); self.assertEqual(self.c.audio(rid,0,"AA==")["error"],"BAD_PCM")
    def test_audio_stall(self):
        self.record(); self.now=3
        self.assertEqual(self.c.tick()["error"],"AUDIO_TIMEOUT")
    def test_max_duration_sleep_resume(self):
        self.record(); self.now=100
        self.assertEqual(self.c.tick()["error"],"MAX_DURATION")
    def test_wav_and_preview(self):
        rid=self.record(); self.c.audio(rid,0,PCM)
        self.assertEqual(self.c.command(dict(op="stop",record_id=rid))["state"],"idle")
        with wave.open(self.c.saved) as f:
            self.assertEqual((f.getframerate(),f.getnchannels(),f.getsampwidth(),f.getnframes()),(48000,2,2,960))
        self.assertTrue(Path(self.c.saved).with_suffix(".json").exists())
    def test_select_is_busy_during_recording(self):
        self.record(); self.c.select("chatgpt")
        self.assertIsNotNone(self.c.writer)
        self.assertEqual(self.c.state,"recording")
        self.assertEqual(self.c.warning,"BUSY")
    def test_duplicate_start_stops_safely(self):
        self.record(); self.assertEqual(self.c.start()["state"],"failed")
        self.assertIsNone(self.c.writer)
    def test_never_enter(self):
        self.ready(); self.assertEqual(self.c.command(dict(op="shortcut",key="enter"))["error"],"KEY_NOT_ALLOWED")
    def test_sparse_audio_rejected_at_stop(self):
        rid=self.record()
        for second in range(4):
            self.now=second; self.c.audio(rid,second,PCM)
        self.now=4
        result=self.c.command(dict(op="stop",record_id=rid))
        self.assertEqual(result["error"],"AUDIO_DURATION_MISMATCH")
        import json
        meta=json.loads(Path(self.c.saved).with_suffix(".json").read_text(encoding="utf8"))
        self.assertEqual(meta["result"],"incomplete")
        self.assertAlmostEqual(meta["audio_seconds"],.08)
    def test_continuous_audio_duration(self):
        rid=self.record()
        for packet in range(200):
            self.now=packet*.02; self.c.audio(rid,packet,PCM)
        self.now=4
        self.assertEqual(self.c.command(dict(op="stop",record_id=rid))["state"],"idle")
    def test_device_audio_overflow_stops_recording(self):
        self.record()
        self.assertEqual(self.c.command(dict(op="abort",error="MIC_BUFFER_OVERFLOW"))["error"],"MIC_BUFFER_OVERFLOW")
        self.assertIsNone(self.c.writer)
    def test_empty_audio_is_not_complete(self):
        rid=self.record()
        self.assertEqual(self.c.command(dict(op="stop",record_id=rid))["error"],"EMPTY_RECORDING")
    def test_fast_foreground_loss_does_not_wait_for_uia(self):
        self.d.quick_check=lambda target: self.d.valid
        self.record();self.c.tick()
        self.now=.001;self.d.valid=False
        self.assertEqual(self.c.tick()["error"],"FOCUS_LOST")
    def test_full_focus_monitor_detects_changed_control(self):
        self.d.quick_check=lambda target: True
        self.record();self.c.tick()
        self.d.valid=False;self.now=.11
        self.assertEqual(self.c.tick()["error"],"FOCUS_LOST")
    def test_stop_always_rechecks_input_control(self):
        self.d.quick_check=lambda target: True
        rid=self.record();self.c.tick()
        self.d.valid=False;self.now=.001
        self.assertEqual(self.c.command(dict(op="stop",record_id=rid))["error"],"FOCUS_LOST")
    def test_long_recording_header_checkpoint_is_readable(self):
        rid=self.record();self.c.audio(rid,0,PCM)
        self.now=1;self.c.audio(rid,1,PCM)
        with wave.open(str(self.c.path)) as reader:
            self.assertEqual(reader.getnframes(),1920)
            self.assertEqual(len(reader.readframes(1920)),7680)
    def test_cancel(self):
        self.record(); self.assertEqual(self.c.command(dict(op="cancel"))["state"],"idle")
        self.assertIsNone(self.c.writer)
    def test_unknown_command(self):
        self.assertEqual(self.c.command(dict(op="shell"))["error"],"UNKNOWN_OP")

if __name__=="__main__": unittest.main()

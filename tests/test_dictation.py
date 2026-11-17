import tempfile,unittest
from pathlib import Path
from smartring.core import Controller
from smartring.dictation import TypelessToggle
from test_guard import Desktop,PCM
class Target(Desktop):
 def __init__(self):self.events=[];self.stamp={"sha256":"before","length":0}
 def typeless_event(self,action,target=None):
  self.events.append(action)
  return dict(self.stamp) if action=="start" else True
 def input_stamp(self,target):return dict(self.stamp)
class Sink:
 error=""
 def __init__(self,events):self.events=events
 def start(self):self.events.append("sink_open")
 def write(self,data):self.events.append("audio")
 def close(self,drain=True):self.events.append("drain" if drain else "abort_audio")
class ToggleTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.d=Target();self.now=0
  self.c=Controller(self.d,self.tmp.name,clock=lambda:self.now,sink_factory=lambda:Sink(self.d.events),dictation_factory=lambda:TypelessToggle(self.d,True))
  self.addCleanup(lambda:self.c.finish("cancelled"))
 def record(self):self.c.select("chatgpt");return self.c.start()["record_id"]
 def test_one_tap_start_then_drain_then_one_tap_stop(self):
  rid=self.record();self.c.audio(rid,0,PCM)
  self.assertEqual(self.c.command(dict(op="stop",record_id=rid))["state"],"processing")
  self.assertEqual(self.d.events,["sink_open","start","audio","drain","stop"])
 def test_processing_waits_for_input_change(self):
  rid=self.record();self.c.audio(rid,0,PCM);self.c.command(dict(op="stop",record_id=rid))
  self.assertEqual(self.c.tick()["state"],"processing")
  self.d.stamp={"sha256":"after","length":10};self.now=.2
  self.assertEqual(self.c.tick()["state"],"idle")
 def test_focus_loss_cancels_instead_of_toggling_again(self):
  rid=self.record();self.d.valid=False
  self.assertEqual(self.c.tick()["error"],"FOCUS_LOST")
  self.assertIn("cancel",self.d.events);self.assertNotIn("stop",self.d.events)
  self.assertEqual(self.c.audio(rid,0,PCM)["error"],"FOCUS_LOST")
 def test_processing_focus_loss_also_cancels(self):
  rid=self.record();self.c.audio(rid,0,PCM);self.c.command(dict(op="stop",record_id=rid))
  self.d.valid=False
  self.assertEqual(self.c.tick()["error"],"FOCUS_LOST")
  self.assertEqual(self.d.events.count("start"),1);self.assertEqual(self.d.events.count("stop"),1)
  self.assertIn("cancel",self.d.events)
 def test_unverified_cancel_prevents_any_hotkey(self):
  self.c.dictation_factory=lambda:TypelessToggle(self.d,False)
  self.c.select("chatgpt")
  self.assertEqual(self.c.start()["error"],"TYPELESS_CANCEL_UNVERIFIED")
  self.assertNotIn("start",self.d.events)
 def test_hotkey_startup_delay_is_not_missing_audio(self):
  import json
  original=self.d.typeless_event
  def delayed(action,target=None):
   if action=='start':self.now+=3
   return original(action,target)
  self.d.typeless_event=delayed
  rid=self.record()
  for i in range(50):
   self.now+=.02;self.c.audio(rid,i,PCM)
  self.c.command(dict(op='stop',record_id=rid))
  self.assertEqual(self.c.state,'processing')
  metadata=json.loads(Path(self.c.saved).with_suffix('.json').read_text(encoding='utf8'))
  self.assertAlmostEqual(metadata['coverage_ratio'],1,places=3)

 def test_later_selection_failure_does_not_mark_previous_record_failed(self):
  import json
  rid=self.record();self.c.audio(rid,0,PCM);self.c.command(dict(op="stop",record_id=rid))
  self.d.stamp={"sha256":"completed","length":10};self.c.tick()
  saved=Path(self.c.saved).with_suffix(".json")
  self.c.select("missing")
  self.assertNotIn("post_record_error",json.loads(saved.read_text(encoding="utf8")))

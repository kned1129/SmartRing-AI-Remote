import struct,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from smartring.virtual_audio import VirtualCableSink,find_virtual_output
from smartring.core import Controller,GuardError
from test_guard import Desktop,PCM

class Stream:
 def __init__(self,**kwargs):self.data=[];self.started=False;self.stopped=False
 def start(self):self.started=True
 def write(self,data):self.data.append(data);return False
 def stop(self):self.stopped=True
 def abort(self):pass
 def close(self):pass

class VirtualTests(unittest.TestCase):
 def make(self):
  stream=Stream();sink=VirtualCableSink(device_resolver=lambda:7,stream_factory=lambda **kw:stream)
  return sink,stream
 def test_queued_audio_is_drained_in_order_without_amplitude_change(self):
  sink,stream=self.make();sink.start()
  for value in range(10):sink.write(struct.pack("<hh",value,0)*960)
  sink.close()
  self.assertEqual(sink.packets_written,10)
  speech=[data for data in stream.data if any(data)]
  self.assertEqual(speech,[struct.pack("<hh",value,value)*960 for value in range(1,10)])
  self.assertGreater(len(stream.data),10)
  self.assertTrue(stream.stopped)
 def test_cancel_before_prebuffer_does_not_play_audio(self):
  sink,stream=self.make();sink.start();sink.write(bytes(3840));sink.close(drain=False)
  self.assertFalse(stream.started);self.assertEqual(stream.data,[])
 def test_missing_cable_never_falls_back_to_speakers(self):
  with patch("sounddevice.query_devices",return_value=[dict(name="Realtek Speakers",max_output_channels=2,hostapi=0)]),patch("sounddevice.query_hostapis",return_value=[dict(name="Windows WASAPI")]):
   with self.assertRaisesRegex(GuardError,"VIRTUAL_DEVICE_UNAVAILABLE"):find_virtual_output()
 def test_route_failure_closes_recording_and_preserves_raw_pcm(self):
  class Broken:
   error=""
   def start(self):pass
   def write(self,data):raise GuardError("VIRTUAL_AUDIO_OVERFLOW")
   def close(self,drain=True):self.drain=drain
  with tempfile.TemporaryDirectory() as tmp:
   sink=Broken();c=Controller(Desktop(),tmp,clock=lambda:0,sink_factory=lambda:sink)
   c.select("chatgpt");rid=c.start()["record_id"]
   self.assertEqual(c.audio(rid,0,PCM)["error"],"VIRTUAL_AUDIO_OVERFLOW")
   self.assertFalse(sink.drain);self.assertIsNone(c.writer)
   self.assertEqual(Path(c.saved).stat().st_size,3884)
 def test_normal_stop_drains_virtual_output(self):
  class Good:
   error=""
   def start(self):pass
   def write(self,data):pass
   def close(self,drain=True):self.drain=drain
  with tempfile.TemporaryDirectory() as tmp:
   sink=Good();c=Controller(Desktop(),tmp,clock=lambda:0,sink_factory=lambda:sink)
   c.select("chatgpt");rid=c.start()["record_id"];c.audio(rid,0,PCM)
   self.assertEqual(c.command(dict(op="stop",record_id=rid))["state"],"idle")
   self.assertTrue(sink.drain)

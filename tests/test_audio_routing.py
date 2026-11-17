import struct,unittest
from smartring.audio import dual_mono_pcm16
class RoutingTests(unittest.TestCase):
 def test_active_left_is_preserved_without_gain_or_clipping(self):
  original=struct.pack("<hhhhhh",-32768,0,12345,0,32767,0)
  self.assertEqual(struct.unpack("<hhhhhh",dual_mono_pcm16(original)),(-32768,-32768,12345,12345,32767,32767))
  self.assertEqual(original,struct.pack("<hhhhhh",-32768,0,12345,0,32767,0))
 def test_invalid_alignment_is_rejected(self):
  with self.assertRaises(ValueError):dual_mono_pcm16(b"123")

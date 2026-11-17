"""Deterministic channel routing; original WAV recordings are never rewritten."""
from array import array
import sys

def dual_mono_pcm16(pcm,channel=0):
    if channel not in (0,1) or len(pcm)%4:raise ValueError("Expected aligned stereo PCM16 and channel 0/1")
    samples=array("h");samples.frombytes(pcm)
    if sys.byteorder!="little":samples.byteswap()
    mono=samples[channel::2]
    samples[0::2]=mono;samples[1::2]=mono
    if sys.byteorder!="little":samples.byteswap()
    return samples.tobytes()

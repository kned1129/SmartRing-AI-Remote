import argparse
import wave
import sounddevice as sd
from smartring.audio import dual_mono_pcm16
p=argparse.ArgumentParser(description="Manual WAV to virtual cable test; no Typeless hotkeys")
p.add_argument("wav")
p.add_argument("--device",type=int,required=True)
p.add_argument("--channel",choices=["left","right","stereo"],default="left",help="Active microphone slot; left was measured on this board")
a=p.parse_args()
d=sd.query_devices(a.device)
if "cable input" not in d["name"].lower() or d["max_output_channels"]<2:
    p.error("Choose the installed CABLE Input playback endpoint")
with wave.open(a.wav) as f:
    if (f.getframerate(),f.getsampwidth(),f.getnchannels()) != (48000,2,2):
        p.error("Expected 48kHz stereo PCM16")
    with sd.RawOutputStream(device=a.device,samplerate=48000,channels=2,dtype="int16") as stream:
        while data:=f.readframes(960):
            if a.channel!="stereo":data=dual_mono_pcm16(data,0 if a.channel=="left" else 1)
            underflow=stream.write(data)
            if underflow: raise RuntimeError("AUDIO_UNDERFLOW")

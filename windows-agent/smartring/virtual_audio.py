"""Explicit VB-CABLE output with bounded buffering; never falls back to speakers."""
import queue,threading,time
from .audio import dual_mono_pcm16
from .core import GuardError

def find_virtual_output():
    import sounddevice as sd
    devices=sd.query_devices();apis=sd.query_hostapis()
    choices=[i for i,d in enumerate(devices)
             if d["name"].lower().startswith("cable input")
             and "vb-audio" in d["name"].lower() and d["max_output_channels"]>=2
             and apis[d["hostapi"]]["name"]=="Windows WASAPI"]
    if len(choices)!=1:raise GuardError("VIRTUAL_DEVICE_UNAVAILABLE")
    return choices[0]

class VirtualCableSink:
    def __init__(self,capacity=25,prebuffer=5,device_resolver=None,stream_factory=None):
        self.queue=queue.Queue(maxsize=capacity)
        self.prebuffer=prebuffer
        self.device_resolver=device_resolver or find_virtual_output
        self.stream_factory=stream_factory
        self.finishing=threading.Event();self.aborted=threading.Event();self.done=threading.Event()
        self.error="";self.stream=None;self.thread=None;self.packets_written=0
    def start(self):
        try:
            factory=self.stream_factory
            if factory is None:
                import sounddevice as sd
                factory=sd.RawOutputStream
            self.stream=factory(device=self.device_resolver(),samplerate=48000,channels=2,
                                dtype="int16",blocksize=960,latency=.1)
            self.thread=threading.Thread(target=self._pump,daemon=True)
            self.thread.start()
        except Exception:
            self.error="VIRTUAL_DEVICE_UNAVAILABLE"
            if self.stream:
                try:self.stream.close()
                except Exception:pass
            raise GuardError(self.error)
    def write(self,pcm):
        if self.error:raise GuardError(self.error)
        if self.finishing.is_set() or self.done.is_set():raise GuardError("VIRTUAL_AUDIO_CLOSED")
        try:self.queue.put_nowait(dual_mono_pcm16(pcm,0))
        except queue.Full:
            self.error="VIRTUAL_AUDIO_OVERFLOW";raise GuardError(self.error)
    def _pump(self):
        try:
            while self.queue.qsize()<self.prebuffer and not self.finishing.is_set():
                if self.aborted.wait(.005):return
            if self.aborted.is_set():return
            self.stream.start()
            # Let the Windows audio engine settle before the first speech sample.
            silence=bytes(3840)
            for _ in range(12):
                if self.aborted.is_set():break
                self.stream.write(silence)
            while not self.aborted.is_set():
                try:data=self.queue.get(timeout=.05)
                except queue.Empty:
                    if self.finishing.is_set():break
                    continue
                underflow=self.stream.write(data)
                self.packets_written+=1
                if underflow and self.packets_written>1:
                    self.error="VIRTUAL_AUDIO_UNDERFLOW";break
            if self.aborted.is_set() or self.error:self.stream.abort()
            else:
                # Push the final speech samples through the engine before closing the endpoint.
                for _ in range(20):self.stream.write(silence)
                self.stream.stop()
        except Exception:
            self.error=self.error or "VIRTUAL_AUDIO_ERROR"
        finally:
            if self.stream:
                try:self.stream.close()
                except Exception:self.error=self.error or "VIRTUAL_AUDIO_ERROR"
            self.done.set()
    def close(self,drain=True):
        if not self.thread:return
        if not drain:self.aborted.set()
        self.finishing.set()
        if not self.done.wait(1.5):
            self.aborted.set();self.error=self.error or "VIRTUAL_AUDIO_TIMEOUT"
        if self.error:raise GuardError(self.error)

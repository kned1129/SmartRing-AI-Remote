"""Mutually exclusive local-microphone/ESP32 routes into the existing CABLE endpoint.

Local audio is forwarded live only: never written to a recording file.
"""
import re
from .core import GuardError
from .virtual_audio import VirtualCableSink

class LocalMicrophoneBridge:
    def __init__(self, device_regex="Realtek", sink_factory=VirtualCableSink):
        self.device_regex=device_regex;self.sink_factory=sink_factory
        self.sink=None;self.stream=None;self.error="";self.frames=0
    def start(self):
        import sounddevice as sd
        from array import array
        devices=sd.query_devices();apis=sd.query_hostapis()
        choices=[i for i,d in enumerate(devices) if d['max_input_channels']>0
                 and re.search(self.device_regex,d['name'],re.I)
                 and 'cable' not in d['name'].lower()
                 and apis[d['hostapi']]['name']=='Windows WASAPI']
        if len(choices)!=1:raise GuardError('LOCAL_MIC_AMBIGUOUS_OR_MISSING')
        channels=min(2,devices[choices[0]]['max_input_channels'])
        def receive(data,frames,timing,status):
            if status:self.error='LOCAL_MIC_INPUT_ERROR';return
            if self.error:return
            pcm=bytes(data)
            if channels==1:
                src=array('h');src.frombytes(pcm);out=array('h')
                for sample in src:out.extend((sample,sample))
                pcm=out.tobytes()
            try:self.sink.write(pcm);self.frames+=frames
            except Exception:self.error='LOCAL_MIC_ROUTE_ERROR'
        try:
            self.sink=self.sink_factory();self.sink.start()
            self.stream=sd.RawInputStream(device=choices[0],samplerate=48000,channels=channels,
                dtype='int16',blocksize=960,latency=.1,callback=receive)
            self.stream.start()
        except Exception:
            self.stop();raise
    def stop(self):
        if self.stream:
            try:self.stream.abort()
            finally:self.stream.close();self.stream=None
        if self.sink:
            sink,self.sink=self.sink,None
            try:sink.close(drain=False)
            except GuardError:
                if not sink.done.is_set():raise

class AudioSourceManager:
    def __init__(self,local_factory=None,remote_factory=VirtualCableSink):
        self.local_factory=local_factory;self.remote_factory=remote_factory
        self.local=None;self.local_error="";self.remote_active=False
    def pause_local(self):
        if self.local:
            bridge,self.local=self.local,None
            bridge.stop()
    def sync(self,state):
        if self.local and (self.local.error or (self.local.sink and self.local.sink.error)):
            self.local_error=self.local.error or self.local.sink.error
            try:self.pause_local()
            except Exception:self.local_error='LOCAL_MIC_STOP_FAILED'
        # Never feed local speech into an ESP32 transcription still processing.
        if self.remote_active or state in ('recording','processing'):return
        if self.local_factory and not self.local and not self.local_error:
            try:
                bridge=self.local_factory();bridge.start();self.local=bridge
            except Exception as e:self.local_error=str(e) or type(e).__name__
    def make_remote(self):
        owner=self
        class Remote:
            sink=None
            @property
            def error(self):return self.sink.error if self.sink else ''
            def start(self):
                owner.pause_local();owner.remote_active=True
                try:self.sink=owner.remote_factory();self.sink.start()
                except Exception:owner.remote_active=False;raise
            def write(self,data):self.sink.write(data)
            def close(self,drain=True):
                try:
                    if self.sink:self.sink.close(drain)
                finally:owner.remote_active=False
        return Remote()
    def close(self):self.pause_local()
    def status(self):
        return dict(local_microphone='routing' if self.local else ('error' if self.local_error else 'off'),
                    local_microphone_error=self.local_error,
                    audio_source='esp32' if self.remote_active else ('windows' if self.local else 'none'))

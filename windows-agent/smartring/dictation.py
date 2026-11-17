"""Typeless is a toggle: one tap starts, another tap stops after PCM is drained."""
from .core import GuardError

class TypelessToggle:
    def __init__(self,desktop,cancel_verified=False):
        self.desktop=desktop;self.cancel_verified=cancel_verified
        self.phase='IDLE';self.baseline=None
    @property
    def active(self):return self.phase in ('STARTING','RECORDING','STOPPING')
    @property
    def processing(self):return self.phase=='PROCESSING'
    def start(self,target):
        if not self.cancel_verified:raise GuardError("TYPELESS_CANCEL_UNVERIFIED")
        if self.active or self.processing:raise GuardError('TYPELESS_BUSY')
        self.phase='STARTING'
        try:
            self.baseline=self.desktop.typeless_event("start",target)
            self.phase='RECORDING'
        except Exception:
            self.cancel();self.phase='FAILED';raise
    def stop(self,target):
        if not self.active:return
        self.phase='STOPPING'
        self.desktop.typeless_event("stop",target)
        self.phase='PROCESSING'
    def poll(self,target):
        if not self.processing:return False
        if self.baseline is None:return False
        changed=self.desktop.input_stamp(target)!=self.baseline
        if changed:self.phase='COMPLETED'
        return changed
    def cancel(self):
        if self.active or self.processing:
            try:self.desktop.typeless_event("cancel")
            finally:self.phase='CANCELLED'

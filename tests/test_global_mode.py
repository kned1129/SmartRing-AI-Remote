import tempfile,unittest
from pathlib import Path
from smartring.core import Controller,GuardError
from smartring.dictation import TypelessToggle
from test_dictation import Target,Sink
from test_guard import PCM

class CurrentDesktop(Target):
    def __init__(self):super().__init__();self.valid=True;self.locates=0;self.captures=0;self.keys=[]
    def locate(self,*args):self.locates+=1;raise AssertionError('Global mode must never locate or focus an application')
    def capture_current(self):
        self.captures+=1
        if not self.valid:raise GuardError('FOCUS_UNAVAILABLE')
        return dict(app='global',hwnd=9,pid=8,title='Current window',session='unknown')
    def global_enter(self,target,combo):
        if not self.valid:raise GuardError('FOCUS_CHANGED')
        self.keys.append((target['hwnd'],combo))

class GlobalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.d=CurrentDesktop();self.now=0
        self.c=Controller(self.d,self.tmp.name,default_mode='global',clock=lambda:self.now,
            sink_factory=lambda:Sink(self.d.events),dictation_factory=lambda:TypelessToggle(self.d,True))
        self.c.set_mode('global');self.addCleanup(lambda:self.c.finish('cancelled'))
    def record(self):return self.c.start()['record_id']
    def test_no_application_selection_required_and_no_locator(self):
        rid=self.record();self.c.audio(rid,0,PCM);self.c.command(dict(op='stop',record_id=rid))
        self.assertEqual(self.c.state,'processing');self.assertEqual(self.d.locates,0)
        self.d.stamp={'sha256':'changed','length':12};self.c.tick()
        self.assertEqual(self.c.state,'idle');self.assertEqual(self.d.events.count('start'),1)
    def test_each_new_record_captures_current_focus(self):
        for i in range(2):
            self.record();self.c.finish('cancelled')
        self.assertEqual(self.d.captures,2);self.assertEqual(self.d.locates,0)
    def test_focus_change_cancels_and_warns(self):
        self.record();self.d.valid=False
        self.assertEqual(self.c.tick()['warning'],'FOCUS_CHANGED_RECORDING_CANCELLED')
        self.assertEqual(self.c.error,'FOCUS_LOST');self.assertIn('cancel',self.d.events)
        self.assertNotIn('stop',self.d.events)
    def test_processing_focus_change_warns_too(self):
        rid=self.record();self.c.audio(rid,0,PCM);self.c.command(dict(op='stop',record_id=rid))
        self.d.valid=False;self.c.tick()
        self.assertEqual(self.c.warning,'FOCUS_CHANGED_RECORDING_CANCELLED')
    def test_enter_deduplicates_before_injection(self):
        self.c.command(dict(op='enter',action_id='one'))
        self.c.command(dict(op='enter',action_id='one'))
        self.assertEqual(self.d.keys,[(9,'enter')]);self.assertEqual(self.d.locates,0)
        self.assertEqual(self.c.status()['enter_injected'],1)
        self.assertEqual(self.c.warning,'DUPLICATE_KEY_IGNORED')
    def test_enter_timeout_cannot_be_retried(self):
        def uncertain(*args):self.d.keys.append('maybe_sent');raise GuardError('UIA_TIMEOUT')
        self.d.global_enter=uncertain
        for _ in range(2):self.c.command(dict(op='enter',action_id='one'))
        self.assertEqual(self.d.keys,['maybe_sent'])
    def test_enter_blocked_during_recording_and_processing(self):
        rid=self.record();self.c.command(dict(op='enter',action_id='one'))
        self.assertEqual(self.c.warning,'BUSY');self.assertEqual(self.d.keys,[])
        self.c.audio(rid,0,PCM);self.c.command(dict(op='stop',record_id=rid))
        self.c.command(dict(op='enter',action_id='two'));self.assertEqual(self.d.keys,[])
    def test_no_mode_switch_during_recording(self):
        self.record();self.c.set_mode('ai')
        self.assertEqual(self.c.input_mode,'global');self.assertEqual(self.c.state,'recording')
    def test_enter_current_focus_failure_never_injects(self):
        self.d.valid=False;self.c.command(dict(op='enter',action_id='one'))
        self.assertEqual(self.d.keys,[]);self.assertEqual(self.c.warning,'FOCUS_UNAVAILABLE')
    def test_ai_rejects_enter(self):
        self.c.set_mode('ai');self.c.command(dict(op='enter',action_id='one'))
        self.assertEqual(self.c.warning,'GLOBAL_ONLY');self.assertEqual(self.d.keys,[])
    def test_ctrl_enter_is_explicit_configuration(self):
        self.c.enter_combo='ctrl+enter';self.c.command(dict(op='enter',action_id='one'))
        self.assertEqual(self.d.keys,[(9,'ctrl+enter')])
    def test_ai_basic_unknown_session_allows_verified_current_composer(self):
        self.c.strict_ai_sessions=True
        self.d.locate=lambda *args:dict(app='codex',session='unknown')
        self.assertEqual(self.c.select('codex')['state'],'ready')
        self.assertEqual(self.c.status()['target_scope'],'application_current')
        self.assertEqual(self.c.status()['session'],'unknown')
    def test_ai_specific_session_still_requires_exact_id(self):
        self.d.locate=lambda *args:dict(app='codex',session='unknown')
        self.assertEqual(self.c.select('codex',scope='specific_session',session_id='expected')['error'],'SESSION_UNVERIFIED')
    def test_mode_switch_clears_old_target(self):
        self.c.target={'hwnd':999};self.c.set_mode('ai');self.c.set_mode('global')
        self.assertIsNone(self.c.target);self.assertEqual(self.c.state,'ready')
    def test_escape_during_processing_cancels_without_second_toggle(self):
        rid=self.record();self.c.audio(rid,0,PCM);self.c.command(dict(op='stop',record_id=rid))
        self.c.command(dict(op='shortcut',key='escape'))
        self.assertEqual(self.c.state,'idle');self.assertEqual(self.d.events.count('stop'),1)
        self.assertIn('cancel',self.d.events)
    def test_ai_can_start_next_toggle_recording_without_relocating(self):
        self.d.locate=lambda *args:dict(app='grokbot',hwnd=9,pid=8,title='Current window',session='verified')
        self.c.select('grokbot')
        rid=self.record();self.c.audio(rid,0,PCM);self.c.command(dict(op='stop',record_id=rid))
        self.d.stamp={'sha256':'completed','length':12};self.c.tick()
        self.assertEqual(self.c.state,'idle')
        self.assertEqual(self.c.start()['state'],'recording')
        self.assertEqual(self.d.events.count('start'),2)
    def test_ai_idle_focus_loss_cannot_restart(self):
        self.c.input_mode='ai';self.c.state='idle';self.c.target={'hwnd':9};self.d.valid=False
        self.assertEqual(self.c.start()['error'],'NOT_READY')

class SourceTests(unittest.TestCase):
    def manager(self):
        from smartring.audio_sources import AudioSourceManager
        self.events=[]
        events=self.events
        class Local:
            error='';sink=None
            def start(self):events.append('local_start')
            def stop(self):events.append('local_stop')
        class Remote:
            error=''
            def start(self):events.append('remote_start')
            def close(self,drain=True):events.append('remote_stop')
        return AudioSourceManager(Local,Remote)
    def test_local_pauses_before_remote_and_resumes_after_processing(self):
        m=self.manager();m.sync('ready');remote=m.make_remote();remote.start()
        self.assertEqual(self.events,['local_start','local_stop','remote_start'])
        remote.close();m.sync('processing');self.assertEqual(self.events[-1],'remote_stop')
        m.sync('idle');self.assertEqual(self.events[-1],'local_start');m.close()
    def test_disabled_local_never_opens_microphone(self):
        m=self.manager();m.local_factory=None;m.sync('idle');self.assertEqual(self.events,[])

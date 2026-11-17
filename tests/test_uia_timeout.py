import json,tempfile,time,unittest
from pathlib import Path
from smartring.safe_desktop import SafeDesktop
from smartring.core import GuardError

WORKER="""import sys,json,time
json.loads(sys.stdin.readline())
print(json.dumps({'id':0,'ok':True}),flush=True)
for line in sys.stdin:
 r=json.loads(line)
 if r.get('app')=='hang' or r.get('target',{}).get('hang'):time.sleep(30)
 print(json.dumps({'id':r['id'],'ok':True,'value':{'app':r.get('app'),'hwnd':1}}),flush=True)
"""
class TimeoutTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  root=Path(self.tmp.name);(root/'logs').mkdir()
  worker=root/'fake_worker.py';worker.write_text(WORKER,encoding='utf8')
  self.d=SafeDesktop({},root,worker_path=worker);self.addCleanup(self.d.close)
 def test_hung_locator_is_killed_and_next_request_recovers(self):
  old=self.d.proc;start=time.monotonic()
  with self.assertRaisesRegex(GuardError,'UIA_TIMEOUT'):self.d.locate('hang')
  self.assertLess(time.monotonic()-start,2.5)
  self.assertIsNotNone(old.poll())
  self.assertEqual(self.d.locate('recovered')['app'],'recovered')
 def test_hung_focus_check_fails_closed(self):
  self.d.quick_check=lambda target:True
  old=self.d.proc;start=time.monotonic()
  self.assertFalse(self.d.check({'hang':True}))
  self.assertLess(time.monotonic()-start,1.2)
  self.assertIsNotNone(old.poll())
 def test_successful_worker_can_be_reused(self):
  self.assertEqual(self.d.locate('codex')['app'],'codex')
  first=self.d.proc.pid
  self.assertEqual(self.d.locate('chatgpt')['app'],'chatgpt')
  self.assertEqual(self.d.proc.pid,first)

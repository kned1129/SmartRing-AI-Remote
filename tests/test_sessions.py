import json,sqlite3,tempfile,unittest,uuid,hashlib
from pathlib import Path
from smartring.sessions import CodexHistory
from smartring.features import DeviceFeatures
from smartring.core import Controller,GuardError
from test_global_mode import CurrentDesktop

class SessionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.home=Path(self.tmp.name);(self.home/'sessions').mkdir()
  self.db=self.home/'state_5.sqlite';self.id=str(uuid.uuid4());self.path=self.home/'sessions/log.jsonl'
  with sqlite3.connect(self.db) as c:
   c.execute('CREATE TABLE threads(id TEXT,title TEXT,rollout_path TEXT,updated_at INTEGER,archived INTEGER,source TEXT,thread_source TEXT)')
   c.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?)',(self.id,'真实测试夹具',str(self.path),100,0,'vscode','user'))
  c.close()
  self.append('第一条回复');self.history=CodexHistory(self.home)
 def append(self,text):
  with self.path.open('a',encoding='utf8') as f:f.write(json.dumps({'type':'response_item','timestamp':'2026-11-10T00:00:00Z','payload':{'type':'message','role':'assistant','phase':'final_answer','content':[{'type':'output_text','text':text}]}},ensure_ascii=False)+'\n')
 def test_real_rows_are_read_without_database_writes(self):
  before=hashlib.sha256(self.db.read_bytes()).digest();page=self.history.list_page()
  self.assertEqual(page['items'][0]['id'],self.id);self.assertEqual(page['items'][0]['verification'],'输入目标待验证')
  self.assertEqual(hashlib.sha256(self.db.read_bytes()).digest(),before)
  with self.history.connection() as c:self.assertEqual(c.execute('pragma query_only').fetchone()[0],1)
 def test_database_connection_rejects_writes(self):
  with self.history.connection() as c:
   with self.assertRaises(sqlite3.OperationalError):c.execute('DELETE FROM threads')
 def test_internal_threads_never_appear(self):
  with sqlite3.connect(self.db) as c:c.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?)',(str(uuid.uuid4()),'guardian',str(self.path),999,0,'{"subagent":{}}','guardian_review'))
  c.close()
  self.assertEqual(self.history.list_page()['total'],1)
 def test_reply_is_actual_assistant_content_not_user_or_tool(self):
  with self.path.open('a',encoding='utf8') as f:f.write(json.dumps({'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'do not display me'}]}})+'\n')
  self.assertEqual(self.history.reply_page(self.id)['body'],'第一条回复')
 def test_multibyte_pagination_reassembles_text(self):
  text='汉字分批加载🙂'*700;self.append(text);offset=0;parts=[]
  while True:
   page=self.history.reply_page(self.id,offset);self.assertLessEqual(len(page['body'].encode('utf8')),1200);parts.append(page['body']);offset=page['next_offset']
   if not page['has_next']:break
  self.assertEqual(''.join(parts),text)
 def test_mid_codepoint_cursor_rejected(self):
  with self.assertRaisesRegex(GuardError,'BAD_REPLY_CURSOR'):self.history.reply_page(self.id,1)
 def test_path_cannot_escape_codex_sessions(self):
  with sqlite3.connect(self.db) as c:c.execute('UPDATE threads SET rollout_path=?',(str(self.home/'auth.json'),))
  c.close()
  with self.assertRaisesRegex(GuardError,'SESSION_PATH_REJECTED'):self.history.reply_page(self.id)
 def test_notification_only_after_real_new_message(self):
  self.history.poll();self.assertIsNone(self.history.notification)
  self.append('第二条回复');self.history.poll();self.assertEqual(self.history.notification['id'],self.id)
  self.history.dismiss();self.history.poll();self.assertIsNone(self.history.notification)
 def test_no_fabricated_reply_when_log_contains_no_assistant(self):
  self.path.write_text('{}\n',encoding='utf8')
  with self.assertRaisesRegex(GuardError,'NO_ASSISTANT_REPLY'):self.history.reply_page(self.id)
 def test_bad_identifier_is_not_a_query(self):
  with self.assertRaisesRegex(GuardError,'BAD_SESSION_ID'):self.history.reply_page("' OR 1=1")
 def test_windows_extended_length_path_is_validated_canonically(self):
  import os
  if os.name!='nt':self.skipTest('Windows path normalization')
  with sqlite3.connect(self.db) as c:c.execute('UPDATE threads SET rollout_path=?',('\\\\?\\'+str(self.path),))
  c.close()
  self.assertEqual(self.history.reply_page(self.id)['body'],'第一条回复')
 def test_user_facing_name_takes_precedence_over_initial_title(self):
  with sqlite3.connect(self.db) as c:
   c.execute('ALTER TABLE threads ADD COLUMN name TEXT');c.execute('UPDATE threads SET name=?',('用户命名的会话',))
  c.close()
  self.assertEqual(self.history.list_page()['items'][0]['title'],'用户命名的会话')

class FeatureTests(SessionTests):
 def setUp(self):
  super().setUp();self.desktop=CurrentDesktop();self.desktop.focused_key=lambda t,k:self.desktop.keys.append(k)
  self.ctl=Controller(self.desktop,self.home/'recordings',default_mode='global');self.ctl.set_mode('global');self.service=DeviceFeatures(self.ctl,self.history)
 def test_unsupported_apps_have_no_fake_sessions(self):
  d=self.service.command({'op':'sessions','app':'chatgpt'})['data'];self.assertTrue(d['unsupported']);self.assertEqual(d['items'],[])
 def test_picking_history_does_not_imply_verified_desktop_target(self):
  d=self.service.command({'op':'select_session','session_id':self.id})['data'];self.assertFalse(d['verified']);self.assertEqual(d['input_error'],'DESKTOP_SESSION_SWITCH_UNAVAILABLE')
 def test_ai_send_cannot_use_unverified_history_selection(self):
  self.ctl.input_mode='ai';self.service.selected=self.history.get(self.id)
  self.assertEqual(self.service.command({'op':'send','action_id':'one'})['service_error'],'SESSION_UNVERIFIED');self.assertEqual(self.desktop.keys,[])
 def test_clipboard_action_deduplicated(self):
  for _ in range(2):self.service.command({'op':'action','key':'copy','action_id':'one'})
  self.assertEqual(self.desktop.keys,['copy'])
 def test_basic_send_works_without_history_and_deduplicates(self):
  self.ctl.input_mode='ai';self.ctl.target=dict(app='codex',scope='application_current')
  for _ in range(2):self.service.command({'op':'send','action_id':'basic'})
  self.assertEqual(self.desktop.keys,['enter'])
 def test_readonly_history_cannot_hijack_basic_send_scope(self):
  self.ctl.input_mode='ai';self.ctl.target=dict(app='codex',scope='application_current')
  self.service.selected=self.history.get(self.id)
  result=self.service.command({'op':'send','scope':'specific_session','action_id':'history'})
  self.assertEqual(result['service_error'],'SESSION_UNVERIFIED');self.assertEqual(self.desktop.keys,[])
 def test_basic_send_rechecks_focus_before_key(self):
  self.ctl.input_mode='ai';self.ctl.target=dict(app='codex',scope='application_current');self.desktop.valid=False
  result=self.service.command({'op':'send','action_id':'wrong'})
  self.assertEqual(result['service_error'],'FOCUS_LOST');self.assertEqual(self.desktop.keys,[])
 def test_service_reads_do_not_interrupt_recording(self):
  self.ctl.state='recording'
  self.assertEqual(self.service.command({'op':'sessions','app':'codex'})['service_error'],'BUSY');self.assertEqual(self.ctl.state,'recording')

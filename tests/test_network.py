import base64
import hashlib
import hmac
import json
import multiprocessing
import socket
import tempfile
import time
import unittest
from pathlib import Path
from smartring.server import run
from test_guard import Desktop
TOKEN="a"*64

def worker(port, directory):
    run(dict(host="127.0.0.1",port=port,token=TOKEN,max_seconds=10),Desktop(),Path(directory))

def failing_status_worker(port,directory):
    import smartring.server as module
    def denied(root,state): raise PermissionError("simulated status file access denied")
    module.write_status=denied
    worker(port,directory)

class NetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        (Path(cls.tmp.name)/"logs").mkdir()
        with socket.socket() as s:
            s.bind(("127.0.0.1",0));cls.port=s.getsockname()[1]
        cls.proc=multiprocessing.Process(target=worker,args=(cls.port,cls.tmp.name))
        cls.proc.start()
        deadline=time.monotonic()+10
        while True:
            try:
                with socket.create_connection(("127.0.0.1",cls.port),timeout=1):break
            except OSError:
                if time.monotonic()>deadline:raise
                time.sleep(.1)
    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate();cls.proc.join(5);cls.tmp.cleanup()
    def connect(self):
        s=socket.create_connection(("127.0.0.1",self.port),timeout=5)
        self.addCleanup(s.close)
        f=s.makefile("rwb",buffering=0);self.addCleanup(f.close)
        challenge=json.loads(f.readline())["challenge"]
        return s,f,challenge
    def call(self,f,**message):
        f.write((json.dumps(dict(v=1,**message))+"\n").encode())
        return json.loads(f.readline())
    def auth(self):
        s,f,ch=self.connect()
        proof=hmac.new(TOKEN.encode(),ch.encode(),hashlib.sha256).hexdigest()
        self.call(f,op="hello",proof=proof)
        return s,f
    def test_roundtrip_pcm(self):
        s,f=self.auth()
        self.assertEqual(self.call(f,op="select",target="codex")["state"],"ready")
        rid=self.call(f,op="start")["record_id"]
        out=self.call(f,op="audio",record_id=rid,seq=0,pcm=base64.b64encode(bytes(3840)).decode())
        self.assertEqual(out["state"],"recording")
        self.assertEqual(self.call(f,op="stop",record_id=rid)["state"],"idle")
    def test_pipelined_audio_stop_preserves_all_frames(self):
        import wave
        sock,f=self.auth()
        self.call(f,op="select",target="codex")
        rid=self.call(f,op="start")["record_id"]
        packet=base64.b64encode(bytes(3840)).decode()
        for seq in range(100):
            message=dict(v=1,id=seq+10,op="audio",record_id=rid,seq=seq,pcm=packet)
            f.write((json.dumps(message)+"\n").encode())
        f.write((json.dumps(dict(v=1,id=1000,op="stop",record_id=rid))+"\n").encode())
        replies=[json.loads(f.readline()) for _ in range(101)]
        self.assertEqual([x["id"] for x in replies],list(range(10,110))+[1000])
        self.assertEqual(replies[-1]["state"],"idle")
        with wave.open(replies[-1]["saved"]) as wav:
            self.assertEqual(wav.getnframes(),96000)
    def test_auth_rejection_and_reconnect(self):
        s,f,ch=self.connect()
        f.write(b'{"v":1,"op":"hello","proof":"invalid"}\n')
        self.assertEqual(f.readline(),b"")
        s,f=self.auth()
        self.assertEqual(self.call(f,op="ping")["state"],"idle")
    def test_bad_version(self):
        s,f,ch=self.connect()
        f.write(b'{"v":99,"op":"hello"}\n')
        self.assertEqual(f.readline(),b"")
    def test_oversized_frame(self):
        s,f=self.auth();f.write(b"x"*9000)
        self.assertEqual(f.readline(),b"")
    def test_disconnect_closes_wav(self):
        s,f=self.auth();self.call(f,op="select",target="codex")
        rid=self.call(f,op="start")["record_id"]
        f.close();s.close();time.sleep(.2)
        meta=Path(self.tmp.name)/"recordings"/(rid+".json")
        self.assertEqual(json.loads(meta.read_text(encoding="utf8"))["result"],"disconnected")
    def test_idle_timeout(self):
        s,f=self.auth();time.sleep(3.4)
        self.assertEqual(f.readline(),b"")

if __name__=="__main__":
    multiprocessing.freeze_support()
    unittest.main()

class TelemetryFailureTests(unittest.TestCase):
 def test_status_file_denied_does_not_stop_audio_service(self):
  with tempfile.TemporaryDirectory() as tmp:
   (Path(tmp)/"logs").mkdir()
   with socket.socket() as probe:probe.bind(("127.0.0.1",0));port=probe.getsockname()[1]
   proc=multiprocessing.Process(target=failing_status_worker,args=(port,tmp));proc.start()
   try:
    deadline=time.monotonic()+5
    while True:
     try:s=socket.create_connection(("127.0.0.1",port),timeout=1);break
     except OSError:
      if time.monotonic()>deadline:raise
      time.sleep(.1)
    with s,s.makefile("rwb",buffering=0) as f:
     ch=json.loads(f.readline())["challenge"]
     def call(**message):
      f.write((json.dumps(dict(v=1,**message))+"\n").encode());return json.loads(f.readline())
     proof=hmac.new(TOKEN.encode(),ch.encode(),hashlib.sha256).hexdigest()
     call(op="hello",proof=proof)
     self.assertEqual(call(op="select",target="codex")["state"],"ready")
     rid=call(op="start")["record_id"]
     call(op="audio",record_id=rid,seq=0,pcm=base64.b64encode(bytes(3840)).decode())
     stop=call(op="stop",record_id=rid)
     self.assertEqual(stop["state"],"idle")
     self.assertTrue(Path(stop["saved"]).exists())
     self.assertEqual(call(op="ping")["state"],"idle")
     self.assertTrue(proc.is_alive())
   finally:proc.terminate();proc.join(5)

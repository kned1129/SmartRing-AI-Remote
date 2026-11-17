"""Real-time 60s TCP stress with synthetic PCM and a deliberately slow focus provider.
No microphone, desktop focus, or Typeless access. Run explicitly, outside normal unit tests.
"""
import base64,hashlib,hmac,json,multiprocessing,queue,secrets,socket,tempfile,threading,time,wave
from pathlib import Path
from smartring.server import run

class SlowDesktop:
 def locate(self,app,hwnd=None):return dict(app=app,hwnd=1,pid=2,title="synthetic",session="unknown")
 def quick_check(self,target):return True
 def check(self,target):time.sleep(.05);return True
 def shortcut(self,key):raise AssertionError("No keyboard actions in stress test")
def worker(port,token,directory):
 run(dict(host="127.0.0.1",port=port,token=token,max_seconds=300),SlowDesktop(),Path(directory))

def main():
 root=Path(__file__).resolve().parents[1]
 with tempfile.TemporaryDirectory() as tmp:
  d=Path(tmp);(d/"logs").mkdir()
  with socket.socket() as free:free.bind(("127.0.0.1",0));port=free.getsockname()[1]
  token=secrets.token_hex(32);process=multiprocessing.Process(target=worker,args=(port,token,tmp));process.start()
  try:
   deadline=time.monotonic()+10
   while True:
    try:s=socket.create_connection(("127.0.0.1",port),timeout=1);break
    except OSError:
     if time.monotonic()>deadline:raise
     time.sleep(.1)
   with s:
    s.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1);s.settimeout(10)
    f=s.makefile("rb")
    challenge=json.loads(f.readline())["challenge"]
    def call(msg):
     s.sendall((json.dumps(dict(v=1,**msg))+"\n").encode());return json.loads(f.readline())
    proof=hmac.new(token.encode(),challenge.encode(),hashlib.sha256).hexdigest()
    call(dict(op="hello",proof=proof,id=1));call(dict(op="select",target="synthetic",id=2))
    start=call(dict(op="start",id=3));rid=start["record_id"]
    done=queue.Queue();acks=[];errors=[]
    def reader():
     try:
      for line in f:
       reply=json.loads(line)
       if reply.get("error"):errors.append(reply["error"])
       if reply.get("id")==90000:done.put(reply);return
       acks.append(reply.get("id"))
     except Exception as e:done.put(e)
    thread=threading.Thread(target=reader,daemon=True);thread.start()
    pcm=base64.b64encode(bytes(3840)).decode();began=time.monotonic();max_lag=0
    for seq in range(3000):
     target=began+seq*.02;delay=target-time.monotonic()
     if delay>0:time.sleep(delay)
     max_lag=max(max_lag,time.monotonic()-target)
     if errors:raise RuntimeError(errors)
     msg=dict(v=1,op="audio",id=seq+10,record_id=rid,seq=seq,pcm=pcm)
     s.sendall((json.dumps(msg)+"\n").encode())
    delay=began+60-time.monotonic()
    if delay>0:time.sleep(delay)
    s.sendall((json.dumps(dict(v=1,op="stop",id=90000,record_id=rid))+"\n").encode())
    stop=done.get(timeout=10)
    if isinstance(stop,Exception):raise stop
    thread.join(1)
    assert not errors,errors
    assert stop["state"]=="idle",stop
    assert acks==list(range(10,3010)),"Missing or reordered replies"
    with wave.open(stop["saved"]) as wav:frames=wav.getnframes();rate=wav.getframerate()
    meta=json.loads(Path(stop["saved"]).with_suffix(".json").read_text(encoding="utf8"))
    assert frames==2880000,(frames,rate)
    assert meta["result"]=="preview" and .95<=meta["coverage_ratio"]<=1.05,meta
    result=dict(passed=True,synthetic=True,real_device=False,focus_provider_delay_ms=50,seconds=frames/rate,packets=len(acks),wall_seconds=meta["wall_seconds"],coverage_ratio=meta["coverage_ratio"],max_send_lag_ms=round(max_lag*1000,2),errors=errors)
    (root/"artifacts/audio-stress-60s.json").write_text(json.dumps(result,indent=2),encoding="utf8")
    print(json.dumps(result),flush=True)
  finally:
   process.terminate();process.join(5)
if __name__=="__main__":
 multiprocessing.freeze_support();main()

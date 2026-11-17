"""Ephemeral runtime telemetry must never interrupt control or audio."""
import hashlib,json,tempfile
from pathlib import Path

def runtime_dir(root):
    identity=hashlib.sha256(str(Path(root).resolve()).lower().encode()).hexdigest()[:12]
    return Path(tempfile.gettempdir())/("SmartRingAIRemote-"+identity)

def write_status(root,state):
    folder=runtime_dir(root);folder.mkdir(parents=True,exist_ok=True)
    temp=folder/"status.tmp"
    temp.write_text(json.dumps(state,ensure_ascii=False),encoding="utf8")
    temp.replace(folder/"status.json")

def read_status(root):
    return json.loads((runtime_dir(root)/"status.json").read_text(encoding="utf8"))

from pathlib import Path
import sys,json
r=Path(__file__).resolve().parents[1];sys.path.insert(0,str(r/'windows-agent'))
from smartring.resident import ensure_agent,resume
resume(r)
print(json.dumps(ensure_agent(r)))

import argparse
import json
import logging
from pathlib import Path
from smartring.desktop import WindowsDesktop
from smartring.server import run

root = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument("--config", type=Path, default=root/"configs/local.json")
p.add_argument("--probe", action="store_true", help="Read-only window/input metadata; no input contents")
p.add_argument("--audio-devices", action="store_true")
a = p.parse_args()
if a.audio_devices:
    import sounddevice
    print(sounddevice.query_devices())
else:
    cfg = json.loads(a.config.read_text(encoding="utf-8-sig"))
    if a.probe:
        desktop = WindowsDesktop(cfg["adapters"])
        result = []
        for app in cfg["adapters"]:
            for w in desktop.windows(app):
                try:
                    w["inputs"] = [dict(name=c.element_info.name, automation_id=c.element_info.automation_id,
                                         control_type=c.element_info.control_type) for c in desktop.inputs(w["hwnd"])]
                except Exception as e: w["error"] = type(e).__name__
                result.append(dict(app=app, **w))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        from smartring.resident import instance
        agent_instance=instance("agent",root)
        if agent_instance is None:raise SystemExit(0)
        from smartring.runtime import runtime_dir
        runtime=runtime_dir(root);runtime.mkdir(parents=True,exist_ok=True)
        from logging.handlers import RotatingFileHandler
        logging.basicConfig(handlers=[RotatingFileHandler(runtime/"agent.log",maxBytes=1000000,backupCount=3,encoding="utf8")],level=logging.INFO)
        from smartring.safe_desktop import SafeDesktop
        desktop=SafeDesktop(cfg["adapters"],root)
        try: run(cfg, desktop, root)
        finally: desktop.close()

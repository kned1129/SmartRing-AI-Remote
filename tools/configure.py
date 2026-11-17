"""Run locally: credentials never go through chat or logs."""
import getpass
import ipaddress
import json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
p=root/"configs/local.json"
if p.exists():c=json.loads(p.read_text(encoding="utf8"))
else:
    import secrets
    c=json.loads((root/"configs/example.json").read_text(encoding="utf8"))
    c["token"]=secrets.token_hex(32)
host=str(ipaddress.IPv4Address(input("Windows LAN IPv4: ").strip()))
ssid=input("2.4GHz WPA2 Wi-Fi SSID: ")
password=getpass.getpass("Wi-Fi password (hidden): ")
if not 1<=len(ssid.encode())<=32:raise ValueError("SSID length")
if not 8<=len(password.encode())<=63:raise ValueError("WPA2 password length")
c["host"]=host
p.write_text(json.dumps(c,ensure_ascii=False,indent=2),encoding="utf8")
values=dict(REMOTE_SSID=ssid,REMOTE_PASSWORD=password,REMOTE_HOST=host,REMOTE_PORT=c["port"],REMOTE_TOKEN=c["token"])
(root/"firmware/main/local_config.h").write_text("".join(f"#define {k} {json.dumps(v,ensure_ascii=True)}\n" for k,v in values.items()),encoding="ascii")
print("Saved locally. Rebuild firmware with tools/build.ps1. Nothing flashed.")

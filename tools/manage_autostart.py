"""Install/remove a reversible current-user Windows logon entry."""
from pathlib import Path
import argparse,sys,json,winreg,subprocess
r=Path(__file__).resolve().parents[1];sys.path.insert(0,str(r/'windows-agent'))
from smartring.resident import process,resume
p=argparse.ArgumentParser();p.add_argument('action',choices=['enable','disable','status']);a=p.parse_args()
keypath=r'Software\Microsoft\Windows\CurrentVersion\Run';name='SmartRing AI Remote'
command=subprocess.list2cmdline([str(r/'.venv/Scripts/pythonw.exe'),str(r/'tools/resident_supervisor.py')])
with winreg.CreateKey(winreg.HKEY_CURRENT_USER,keypath) as key:
    if a.action=='enable':
        winreg.SetValueEx(key,name,0,winreg.REG_SZ,command);resume(r)
        if not process(r,'supervisor'):subprocess.Popen([str(r/'.venv/Scripts/pythonw.exe'),str(r/'tools/resident_supervisor.py')],cwd=r,creationflags=subprocess.CREATE_NO_WINDOW)
    elif a.action=='disable':
        try:winreg.DeleteValue(key,name)
        except FileNotFoundError:pass
        watcher=process(r,'supervisor')
        if watcher:watcher.terminate();watcher.wait(5)
    try:value=winreg.QueryValueEx(key,name)[0]
    except FileNotFoundError:value=None
print(json.dumps(dict(enabled=value==command,command_matches_project=value==command,per_user=True,admin_required=False)))

$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$py=Join-Path $root '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $py)) {python -m venv (Join-Path $root '.venv'); if($LASTEXITCODE -ne 0){throw 'venv failed'}}
& $py -m pip install -r (Join-Path $root 'windows-agent\requirements.txt')
if($LASTEXITCODE -ne 0){throw 'Dependencies failed'}
if (!(Test-Path -LiteralPath (Join-Path $root 'configs\local.json'))) {
    & $py -c "import json,secrets,pathlib; r=pathlib.Path(r'$root'); c=json.loads((r/'configs/example.json').read_text(encoding='utf8')); c['token']=secrets.token_hex(32); (r/'configs/local.json').write_text(json.dumps(c,ensure_ascii=False,indent=2),encoding='utf8')"
}
Write-Host 'Installed. Run Start.cmd (no administrator required).'

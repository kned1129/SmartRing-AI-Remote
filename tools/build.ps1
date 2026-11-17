param(
    [string]$Stage = (Join-Path $env:TEMP 'SmartRingAIRemote-build'),
    [string]$IdfExport = '',
    [string]$IdfToolsPath = ''
)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$source=Join-Path $root 'firmware'
New-Item -ItemType Directory -Force $Stage | Out-Null
$marker=Join-Path $Stage '.ai-remote-build'
if ((Get-ChildItem -LiteralPath $Stage -Force).Count -gt 0 -and !(Test-Path -LiteralPath $marker)) { throw 'Stage is not owned by this project' }
Set-Content -LiteralPath $marker -Value 'SmartRing AI Remote generated build files'
foreach($file in @('CMakeLists.txt','partitions.csv','sdkconfig.defaults','dependencies.lock')) { Copy-Item -LiteralPath (Join-Path $source $file) -Destination $Stage -Force }
New-Item -ItemType Directory -Force (Join-Path $Stage 'main') | Out-Null
Copy-Item -Path (Join-Path $source 'main\*') -Destination (Join-Path $Stage 'main') -Force
# Dependencies are resolved by ESP-IDF from the checked-in component lock.
# A fresh checkout must not require another private project as a cache source.
if ($IdfToolsPath) { $env:IDF_TOOLS_PATH=$IdfToolsPath }
if ($IdfExport) {
    if (!(Test-Path -LiteralPath $IdfExport)) { throw 'ESP-IDF export script not found' }
    . $IdfExport
} elseif (!(Get-Command idf.py -ErrorAction SilentlyContinue)) {
    $candidate = if ($env:IDF_PATH) { Join-Path $env:IDF_PATH 'export.ps1' } else { 'C:\Espressif\frameworks\esp-idf-v5.5.5\export.ps1' }
    if (!(Test-Path -LiteralPath $candidate)) { throw 'Open an ESP-IDF PowerShell terminal, or pass -IdfExport <path to export.ps1> and optionally -IdfToolsPath <tools directory>.' }
    . $candidate
}
Push-Location $Stage
try {
    Copy-Item -LiteralPath (Join-Path $Stage "sdkconfig.defaults") -Destination (Join-Path $Stage "sdkconfig") -Force
    idf.py reconfigure
    if ($LASTEXITCODE -ne 0) { throw "ESP-IDF reconfigure failed" }
    idf.py build
    if ($LASTEXITCODE -ne 0) { throw 'ESP-IDF build failed' }
    $effective=Get-Content -LiteralPath (Join-Path $Stage 'sdkconfig') -Raw
    if ($effective -match '(?m)^CONFIG_(SECURE_BOOT|SECURE_FLASH_ENC_ENABLED)=y') {throw 'Unsafe config'}
    $dist=Join-Path $root 'artifacts\firmware'
    New-Item -ItemType Directory -Force $dist | Out-Null
    foreach($file in @('smartring_ai_remote.bin','smartring_ai_remote.elf','flasher_args.json','bootloader\bootloader.bin','partition_table\partition-table.bin')) {Copy-Item -LiteralPath (Join-Path $Stage "build\$file") -Destination $dist -Force}
    Copy-Item -LiteralPath (Join-Path $Stage 'sdkconfig') -Destination (Join-Path $dist 'sdkconfig.effective') -Force
    Get-ChildItem -LiteralPath $dist -File | Where-Object Name -ne SHA256SUMS.txt | ForEach-Object { "$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash)  $($_.Name)" } | Set-Content -LiteralPath (Join-Path $dist 'SHA256SUMS.txt')
} finally { Pop-Location }
Write-Host 'Build only; no device flashed.'

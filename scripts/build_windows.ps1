# Build the FPGA Virtual Lab Windows distribution (dist\FPGAVirtualLab\FPGAVirtualLab.exe).
#
#   powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 [-IverilogDir C:\iverilog] [-SkipTests]
#
# Steps: venv + dependencies -> (re)build fvl_init.vpi if gcc is available -> tests ->
#        PyInstaller -> bundle Icarus Verilog into tools\iverilog -> headless self-test of the exe.
param(
    [string]$IverilogDir = "C:\iverilog",
    [switch]$SkipTests
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step($msg) { Write-Host "`n=== $msg" -ForegroundColor Cyan }

Step "Python environment"
if (-not (Test-Path ".venv\Scripts\python.exe")) { py -3.12 -m venv .venv }
$Py = Join-Path $Root ".venv\Scripts\python.exe"
& $Py -m pip install --upgrade pip | Out-Null
& $Py -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Step "Icarus Verilog"
if (-not (Test-Path "$IverilogDir\bin\iverilog.exe")) {
    throw "Icarus Verilog not found in $IverilogDir. Install it (winget install Icarus.Verilog or https://bleyer.org/icarus/) or pass -IverilogDir."
}
& "$IverilogDir\bin\iverilog.exe" -V 2>&1 | Select-Object -First 1

Step "Power-on init VPI module (native\fvl_init.vpi)"
$gcc = Get-Command gcc -ErrorAction SilentlyContinue
if ($gcc) {
    & gcc -c -O2 -I"$IverilogDir\include\iverilog" native\fvl_init.c -o native\fvl_init.o
    & gcc -shared -o native\fvl_init.vpi native\fvl_init.o -L"$IverilogDir\lib" -lvpi -static-libgcc
    if ($LASTEXITCODE -ne 0) { throw "building fvl_init.vpi failed" }
    Remove-Item native\fvl_init.o -ErrorAction SilentlyContinue
    Write-Host "rebuilt native\fvl_init.vpi"
} elseif (Test-Path native\fvl_init.vpi) {
    Write-Host "gcc not found - using the prebuilt native\fvl_init.vpi"
} else {
    throw "native\fvl_init.vpi missing and gcc not available to build it"
}

Step "Generated assets"
& $Py scripts\gen_board_svg.py
& $Py scripts\gen_icon.py

if (-not $SkipTests) {
    Step "Tests (real simulator)"
    $env:FVL_IVERILOG_DIR = $IverilogDir
    & $Py -m pytest tests -q
    if ($LASTEXITCODE -ne 0) { throw "tests failed" }
}

Step "PyInstaller"
& $Py -m PyInstaller --noconfirm --clean FPGAVirtualLab.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Step "Bundling Icarus Verilog into dist\FPGAVirtualLab\tools\iverilog"
$Dest = "dist\FPGAVirtualLab\tools\iverilog"
New-Item -ItemType Directory -Force $Dest | Out-Null
foreach ($d in "bin", "lib", "include") {
    Copy-Item -Recurse -Force "$IverilogDir\$d" "$Dest\$d"
}
Set-Content -Path "$Dest\README.txt" -Encoding utf8 -Value @"
Icarus Verilog (https://steveicarus.github.io/iverilog/) is free software licensed under the
GNU GPL v2 or later. This folder contains an unmodified binary build copied from $IverilogDir.
Source code: https://github.com/steveicarus/iverilog
"@

Step "Self-test of the packaged executable"
$exe = Resolve-Path "dist\FPGAVirtualLab\FPGAVirtualLab.exe"
# Make sure the bundled simulator (not a system install) is used.
Remove-Item Env:FVL_IVERILOG_DIR -ErrorAction SilentlyContinue
$p = Start-Process -FilePath $exe -ArgumentList "--selftest" -Wait -PassThru
$report = Join-Path $env:APPDATA "FPGAVirtualLab\selftest.json"
if (Test-Path $report) { Get-Content $report }
if ($p.ExitCode -ne 0) { throw "packaged self-test FAILED (exit code $($p.ExitCode))" }

Step "Portable zip"
$ver = (& $Py -c "import app; print(app.__version__)").Trim()
$zip = "dist\FPGAVirtualLab-$ver-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path "dist\FPGAVirtualLab\*" -DestinationPath $zip
Write-Host "wrote $zip"

Step "Installer (Inno Setup)"
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    & $iscc /Qp installer\FPGAVirtualLab.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
    Write-Host "installer written to dist\"
} else {
    Write-Host "Inno Setup 6 not found - installer skipped (winget install JRSoftware.InnoSetup, then re-run)." -ForegroundColor Yellow
}
Write-Host "`nBuild OK: $exe" -ForegroundColor Green

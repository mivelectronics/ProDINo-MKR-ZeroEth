# Builds dist\ProDINo-MKR-Tester\ProDINo-MKR-Tester.exe (folder build, no console) with PyInstaller,
# then zips the folder to dist\ProDINo-MKR-Tester.zip.
# Folder build on purpose: the single-file variant trips Windows Defender's ML heuristic
# (Trojan:Win32/Sabsik.*!ml) because of PyInstaller's self-extracting stub.
# Bundles: bossac.exe 1.7.0 (Arduino flavour, from PlatformIO's tool-bossac@1.10700) and the
# latest firmware.bin (from ..\..\.pio\build\prodino_mkr, i.e. run "pio run" first).
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Resolve-Path (Join-Path $here "..\..")
Push-Location $here
try {
    New-Item -ItemType Directory -Force resources | Out-Null
    $bossac = Get-ChildItem "$env:USERPROFILE\.platformio\packages\tool-bossac@1.10700*\bossac.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($bossac) { Copy-Item -Force $bossac.FullName resources\bossac.exe }
    elseif (-not (Test-Path resources\bossac.exe)) { throw "bossac 1.7.0 not found - run 'pio run -t upload' once so PlatformIO installs it" }
    $fw = Join-Path $root ".pio\build\prodino_mkr\firmware.bin"
    if (Test-Path $fw) { Copy-Item -Force $fw resources\firmware.bin } else { Write-Warning "no firmware.bin - run 'pio run' first; using the one already in resources\" }

    python -m pip install --quiet pyinstaller pyserial
    python -m PyInstaller --noconfirm --clean --onedir --windowed --name ProDINo-MKR-Tester `
        --add-data "resources\*;." prodino_tester.py
    if (Test-Path dist\ProDINo-MKR-Tester.zip) { Remove-Item dist\ProDINo-MKR-Tester.zip }
    Compress-Archive -Path dist\ProDINo-MKR-Tester -DestinationPath dist\ProDINo-MKR-Tester.zip
    Get-Item dist\ProDINo-MKR-Tester\ProDINo-MKR-Tester.exe, dist\ProDINo-MKR-Tester.zip | Select-Object Name, Length
} finally { Pop-Location }

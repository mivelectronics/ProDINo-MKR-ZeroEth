# ProDINo-MKR-Tester

Windows tool for flashing and testing the ProDINo MKR Zero Ethernet v11 board. Ships as a
folder (`dist\ProDINo-MKR-Tester\ProDINo-MKR-Tester.exe` + `_internal\`, zipped): unzip
anywhere and run, no install, no Python needed on the target PC.

It is deliberately **not** a single-file exe: PyInstaller's one-file stub is flagged by
Windows Defender's ML heuristic as `Trojan:Win32/Sabsik.*!ml` (false positive).

| Tab | What it does |
|---|---|
| **Flash** | application over USB: console `boot` (or 1200 bps touch) → bootloader port `2341:0052` → bundled `bossac.exe` (erase, write, verify, reset). Firmware can be replaced with *Browse...* |
| **I/O** | relay buttons (live state), opto input indicators (live), status LED, J14 pin set/read, J14 UART loopback |
| **Peripherals** | Ethernet (W5500 probe, DHCP, link), Grove J10 I2C scan + OLED, board info |
| **RS485** | init at a baud rate, board sends text; optional USB-RS485 adapter on the PC for a two-way test |
| **Run all** | automatic sequence with PASS/FAIL report; checks that need a fixture (cable, OLED, adapter, J14 plug) are ticked on/off and reported as *skip* |
| **Terminal** | every command/reply; type raw console commands (`?` for help) |

The board is auto-detected (USB `2341:8052`) and connected on start.

## Build the exe
```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```
Needs Python 3.10+ with `pyserial` and `pyinstaller` (installed by the script), a fresh
`pio run` in the project root (bundles the current `firmware.bin`) and PlatformIO's
`tool-bossac@1.10700` (bossac 1.7.0, installed by the first `pio run -t upload`).
Run it with `-File` from a separate PowerShell: pip writes notices to stderr, which the
script's `$ErrorActionPreference = "Stop"` would treat as fatal inside an existing session.

## Run from source
```powershell
pip install pyserial
python prodino_tester.py
```

## Notes
- bossac must be the **Arduino 1.7.0** build: it knows the SAMD21 SAM-BA bootloader and
  writes at 0x2000 on its own (bossac 1.9 needs `--offset=0x2000`).
- RS485 two-way test: adapter A→J6.2 (A), B→J6.1 (B); it sends `MIV-BOARD` board→PC and
  `MIV-PC` PC→board at the baud rate chosen on the RS485 tab.

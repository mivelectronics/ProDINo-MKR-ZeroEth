# ProDINo MKR Zero Ethernet - test firmware

Board: **ProDINo MKR Zero Ethernet v11** (KMP Electronics, built by MIV Electronics; Altium
project `ProDINoZERO-ETHv11`), MCU **ATSAMD21G18A** @ 48 MHz. 4 relays, 4 opto-isolated
inputs, W5500 Ethernet, RS485 (ST3485), Grove I2C, add-on header J14, micro-USB.

The board is pin compatible with the **Arduino MKR GSM 1400**, so it builds with the stock
Arduino SAMD core and the `mkrgsm1400` variant - no custom variant, no custom bootloader.

```
platformio.ini            PlatformIO project (Arduino framework, board mkrgsm1400, PMIC flag removed)
src/main.cpp              bring-up / test firmware (USB console)
docs/pinmap.md            full pin map (from the PCB netlist) + hardware notes
tools/tester/             ProDINo-MKR-Tester: Windows exe to flash the app and test everything
```

## Quick start

1. The board already carries the KMP (Arduino SAM-BA) bootloader. Build & upload over USB
   (1200 bps touch + BOSSA, no buttons):
   ```bash
   pio run -t upload
   ```
   Alternative over SWD (J-Link on J11): `pio run -e prodino_mkr_jlink -t upload`.
2. **Console**: `pio device monitor` (115200) - `?` lists the commands (relays, inputs, LED,
   Ethernet, RS485, I2C/OLED, J14). Every reply ends with `OK`/`ERR`.
3. **Or use the GUI**: `ProDINo-MKR-Tester.zip` (unzip, run `ProDINo-MKR-Tester.exe`) does 1-2
   with buttons and a "Run all" PASS/FAIL report.

### Manual bootloader entry
Double-tap the RESET button (B1) → the port `Arduino MKR GSM 1400 bootloader` (2341:0052)
appears; `pio run -t upload --upload-port COMx` or the tester's *Flash application*.

### Blank chip
Flash the Arduino MKR GSM 1400 bootloader (`samd21_sam_ba_arduino_mkrgsm1400.bin` from the
Arduino SAMD core) over SWD on J11, then continue with step 1.

## Test firmware (src/main.cpp)

USB console, every reply ends with `OK`/`ERR`, events start with `EV `. Commands: `?` `v` `st`
`r` `r<n> <0|1>` `r all` `i` `t` `l` `s` `d` `e` `e dhcp` `e link` `x init|off|send` `g` `u`
`reset` `boot`.

Verified on hardware (2026-09-24, fw 0.1.0): console, relays (all/walk), status LED,
W5500 (VERSIONR 0x04, MAC r/w, link 100M full, DHCP), RS485 transmit, bootloader entry
and flashing from the tester. Tester "Run all": 11/11 PASS (OLED, RS485 two-way and J14
loopback need their fixtures and were skipped).

## Hardware notes

- The RS485 transceiver has DE and /RE tied (D3): the board cannot hear its own frames. The
  two-way test needs a USB-RS485 adapter on J6 (tester, RS485 tab).
- Opto inputs read LOW when energised; the firmware reports `1` = energised.
- PA18 (USB host enable) is never driven - HIGH would put 5 V out on the micro-USB.
- No battery charger/PMIC on this board, hence `build_unflags = -DUSE_BQ24195L_PMIC`.

## USB identity

Application `2341:8052`, bootloader `2341:0052` (Arduino MKR GSM 1400 IDs), product string
"ProDINo MKR Zero Ethernet", manufacturer "KMP Electronics". Windows loads its own CDC driver.

## Toolchain

PlatformIO (`atmelsam` platform, `framework-arduino-samd`, `tool-bossac` 1.7.0).

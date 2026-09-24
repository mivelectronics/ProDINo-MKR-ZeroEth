# ProDINo MKR Zero Ethernet v11 - pin map

Source: the Altium board `ProDINoZERO-ETHv11.PcbDoc` (pad → net table of U3, traced to the
peripherals). Cross-checked against KMP's `KMPProDinoMKRZero` library (same pins).

MCU **U3 = ATSAMD21G18A-AU**, 48 MHz, 32.768 kHz crystal Y2 on PA00/PA01.
The board uses exactly the **Arduino MKR GSM 1400** pin assignment (net names `D0..D14`,
`A0..A6`, `MODEM_*`), so the stock `mkrgsm1400` variant is used; "Arduino pin" below is
its numbering.

| Function | Arduino pin | Port | U3 pin | Goes to |
|---|---|---|---|---|
| Relay 1 | A6 (21) | PA07 | 12 | U1.1 ULN2003 → REL1 (J2) |
| Relay 2 | A5 (20) | PA06 | 11 | U1.2 → REL2 (J2) |
| Relay 3 | A4 (19) | PA05 | 10 | U1.3 → REL3 (J3) |
| Relay 4 | A3 (18) | PA04 | 9 | U1.4 → REL4 (J3) |
| Opto IN1 | A1 (16) | PB02 | 47 | U9 TLP185 ← J9.8; LED D1 |
| Opto IN2 | D7 | PA21 | 30 | U10 ← J9.6; LED D2 |
| Opto IN3 | D0 | PA22 | 31 | U11 ← J9.4; LED D3 |
| Opto IN4 | D1 | PA23 | 32 | U12 ← J9.2; LED D4 |
| Status LED | D6 (`LED_BUILTIN`) | PA20 | 29 | R32 → LED |
| W5500 MOSI | D8 | PA16 | 25 | U2.35 (SERCOM1 PAD0) |
| W5500 SCK | D9 | PA17 | 26 | U2.33 (SERCOM1 PAD1) |
| W5500 MISO | D10 | PA19 | 28 | U2.34 (SERCOM1 PAD3) |
| W5500 SCSn | D4 | PB10 | 19 | U2.32, pull-up in R9 |
| W5500 RSTn | D5 | PB11 | 20 | U2.37, pull-up in R9 |
| W5500 INTn | D2 | PA10 | 15 | U2.36, pull-up in R9 |
| RS485 RO | D13 / Serial1 RX | PB23 | 38 | U5.1 ST3485 |
| RS485 DI | D14 / Serial1 TX | PB22 | 37 | U5.4 |
| RS485 DE + /RE | D3 | PA11 | 16 | U5.2 + U5.3 (tied, HIGH = transmit) |
| I2C SDA | D11 / Wire | PA08 | 13 | Grove J10.2, ESD D19 |
| I2C SCL | D12 / Wire | PA09 | 14 | Grove J10.1, ESD D20 |
| USB D-/D+ | - | PA24/PA25 | 33/34 | micro-USB J12 |
| USB host enable | 24 | PA18 | 27 | R38 → USB ID / Q4 → Q12 → Q2: switches 5 V **out** to J12 |
| SWDIO / SWCLK | - | PA31/PA30 | 46/45 | J11 (2×3: 1=3V3 2=SWDIO 3=RESET 4=SWCLK 5=GND) |
| RESET | - | - | 40 | button B1, J11.3 |
| unused | A0 (15), A2 (17), AREF | PA02, PB03, PA03 | 3, 48, 4 | not connected |

## Add-on header J14 (11 pins, for the GSM/LoRa/NB add-on boards)

| J14 | Net | Port | Arduino pin | Peripheral |
|---|---|---|---|---|
| 1 | 5V | | | |
| 2 | GND | | | |
| 3 | 3V3 | | | |
| 4 | MODEM_CTS | PA15 | 29 | Serial2 CTS (SERCOM4) |
| 5 | MODEM_RTS | PA14 | 28 | Serial2 RTS |
| 6 | MODEM_RX | PA13 | 27 | Serial2 RX |
| 7 | MODEM_TX | PA12 | 26 | Serial2 TX |
| 8 | MODEM_DTR | PA28 | 35 | GSM_DTR (core drives LOW at boot) |
| 9 | MODEM_RESET | PB08 | 31 | GSM_RESETN (core drives HIGH at boot) |
| 10 | VBATT/BOOT0 | PB09 | 32 | ADC_VBAT in the variant |
| 11 | BATT_INT/RF_NRST | PA27 | 30 | PMIC_IRQ in the variant |

Loopback plug for the test: J14.4↔J14.5 (RTS→CTS) and J14.6↔J14.7 (TX→RX).

## Other connectors

- **J2 / J3** relay terminals (5.08 mm, 6 pins each: COM/NO/NC of REL1+REL2 and REL3+REL4, varistors VR1..VR8).
- **J6** power + RS485 (3.5 mm, 5 pins): 1 = B, 2 = A, 4 = VIN (through D7/F2/TVS D11 to U4 AP64352 → 5 V).
- **J9** opto inputs (3.5 mm, 8 pins): the optocouplers sit on J9.8 (IN1, U9), J9.6 (IN2, U10),
  J9.4 (IN3, U11), J9.2 (IN4, U12); the odd pins J9.7/.5/.3/.1 go to the input stages Q1/Q6/Q8/Q10.
- **J10** Grove: 1 SCL, 2 SDA, 3 VCC, 4 GND.
- **J1** RJ45 with magnetics (W5500, 25 MHz crystal Y1).

## Firmware notes

- Inputs read **LOW when energised** (opto pulls the line down); the firmware reports 1 = energised.
- `USE_BQ24195L_PMIC` is removed from the build (`build_unflags`): there is no PMIC; with the
  flag the core toggles PB09 (J14.10) and writes I2C registers at 0x6B on the Grove bus at boot.
- PA18 is never driven: HIGH would turn J12 into a 5 V source (USB host mode).
- Bootloader: Arduino SAM-BA (MKR GSM 1400), USB `2341:0052`, app at `0x2000`, application
  enumerates as `2341:8052`. Double-tap RESET or the magic `0x07738135` in the last RAM word
  keeps it in the bootloader.

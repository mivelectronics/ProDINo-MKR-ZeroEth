/*
  ProDINo MKR Zero Ethernet v11 - board bring-up / test firmware

  Line-oriented USB CDC console (any baud). Every command answers with lines of
  text and finishes with "OK" or "ERR <reason>", so a host program can drive it.
  Asynchronous events (input change, RS485 frame received) are prefixed "EV ".

    ?                      help
    v                      firmware / board info
    st                     one-line status  (ST rel=.. in=.. up=.. eth=.. rs485=.. oled=..)
    r                      relay states
    r<n> <0|1>             set relay n (1..4)             r all <0|1>
    i                      opto input states (1 = energised)
    t                      relay walk test
    l <0|1|auto>           status LED off / on / heartbeat
    s                      I2C scan on Wire (Grove J10)
    d                      OLED on J10 (SSD1306 128x64 @0x3C): test pattern + board status
    d <text>               show text on the OLED            d off -> clear
    e                      W5500 probe (SPI): version, MAC r/w, PHY link
    e dhcp                 Ethernet stack: DHCP, print IP/GW/DNS      e link: PHY link
    x init [baud]          RS485 (Serial1 + DE on D3) up, default 9600    x off
    x send <text>          transmit text + CRLF on RS485; received bytes come as "EV 485RX"
    g <j14pin> <0|1|i|r>   add-on header J14 pins 4..11: output 0/1, input w/ pull-up, read
    u                      J14 UART loopback (Serial2; plug jumpers J14.4-5 and J14.6-7)
    reset                  reboot
    boot                   reboot into the bootloader (double-tap magic)
*/

#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>
#include <Ethernet.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#define FW_VERSION "0.1.0"

// ---------------------------------------------------------------- board pins (MKR numbering)
#define RELAY_COUNT 4
#define INPUT_COUNT 4
static const uint8_t RELAY_PINS[RELAY_COUNT] = { A6, A5, A4, A3 };   // PA07..PA04 -> ULN2003 -> REL1..4
static const uint8_t INPUT_PINS[INPUT_COUNT] = { A1, 7, 0, 1 };      // PB02, PA21, PA22, PA23 <- TLP185 U9..U12
#define PIN_ETH_CS   4    // PB10  W5500 SCSn
#define PIN_ETH_RST  5    // PB11  W5500 RSTn
#define PIN_ETH_INT  2    // PA10  W5500 INTn
#define PIN_485_DE   3    // PA11  ST3485 DE + /RE (HIGH = transmit)
// J14 (add-on header) pin number -> Arduino pin. J14.1 5V, .2 GND, .3 3V3.
static const uint8_t J14_PINS[12] = { 0xFF, 0xFF, 0xFF, 0xFF,
                                      29,   // .4  PA15 MODEM_CTS  (Serial2 CTS)
                                      28,   // .5  PA14 MODEM_RTS  (Serial2 RTS)
                                      27,   // .6  PA13 MODEM_RX   (Serial2 RX)
                                      26,   // .7  PA12 MODEM_TX   (Serial2 TX)
                                      35,   // .8  PA28 MODEM_DTR
                                      31,   // .9  PB08 MODEM_RESET
                                      32,   // .10 PB09 VBATT/BOOT0
                                      30 }; // .11 PA27 BATT_INT/RF_NRST
// PA18 (pin 24, USB host enable) switches 5 V onto the USB connector - never driven here.

static bool ethOk = false;      // W5500 answered on SPI at least once
static bool rs485Up = false;
static long rs485Baud = 9600;

static Adafruit_SSD1306 oled(128, 64, &Wire, -1);
static bool oledOk = false;

static bool    ledAuto = true;
static uint8_t lastInputs = 0xFF;

// ---------------------------------------------------------------- helpers
static void ok()               { Serial.println("OK"); }
static void err(const char *m) { Serial.print("ERR "); Serial.println(m); }

static uint8_t readInputs() {
  uint8_t v = 0;
  for (uint8_t i = 0; i < INPUT_COUNT; i++)
    if (digitalRead(INPUT_PINS[i]) == LOW) v |= (1 << i);   // opto pulls LOW when energised
  return v;
}
static uint8_t readRelays() {
  uint8_t v = 0;
  for (uint8_t i = 0; i < RELAY_COUNT; i++) if (digitalRead(RELAY_PINS[i])) v |= (1 << i);
  return v;
}
static void printBits(const char *label, uint8_t v, uint8_t n) {
  Serial.print(label);
  for (uint8_t i = 0; i < n; i++) { Serial.print((v >> i) & 1); Serial.print(' '); }
  Serial.println();
}

static void hexByte(uint8_t b) { if (b < 16) Serial.print('0'); Serial.print(b, HEX); }

static uint8_t ethRead8(uint16_t addr);
static const uint16_t W5500_SHAR = 0x0009, W5500_PHYCFGR = 0x002E, W5500_VERSIONR = 0x0039;

// ---------------------------------------------------------------- commands
static void cmdInfo() {
  Serial.println("board: ProDINo MKR Zero Ethernet v11 (ATSAMD21G18A)");
  Serial.print("fw: "); Serial.print(FW_VERSION); Serial.print("  built "); Serial.print(__DATE__); Serial.print(' '); Serial.println(__TIME__);
  Serial.print("core: Arduino SAMD (mkrgsm1400 variant), F_CPU="); Serial.println(F_CPU);
  uint32_t *w0 = (uint32_t *)0x0080A00C, *w1 = (uint32_t *)0x0080A040;
  Serial.print("uid: "); Serial.print(w0[0], HEX); Serial.print(w1[0], HEX); Serial.print(w1[1], HEX); Serial.println(w1[2], HEX);
  Serial.print("reset cause: 0x"); Serial.println(PM->RCAUSE.reg, HEX);   // 1=POR 2=BOD12 4=BOD33 0x10=EXT 0x20=WDT 0x40=SYST
  Serial.print("uptime ms: "); Serial.println(millis());
  ok();
}

static void cmdStatus() {
  Serial.print("ST rel=");
  for (uint8_t i = 0; i < RELAY_COUNT; i++) Serial.print((readRelays() >> i) & 1);
  Serial.print(" in=");
  for (uint8_t i = 0; i < INPUT_COUNT; i++) Serial.print((lastInputs >> i) & 1);
  Serial.print(" up="); Serial.print(millis());
  Serial.print(" eth="); Serial.print(!ethOk ? "-" : (ethRead8(W5500_PHYCFGR) & 1 ? "link" : "nolink"));
  Serial.print(" rs485="); if (rs485Up) Serial.print(rs485Baud); else Serial.print("off");
  Serial.print(" oled="); Serial.print(oledOk ? 1 : 0);
  Serial.println();
  ok();
}

static void cmdRelay(char *arg) {
  while (*arg == ' ') arg++;
  if (*arg == 0) { printBits("REL: ", readRelays(), RELAY_COUNT); ok(); return; }
  int n = 0, v = 0;
  if (strncmp(arg, "all", 3) == 0) {
    if (sscanf(arg + 3, "%d", &v) != 1) { err("usage: r all <0|1>"); return; }
    for (uint8_t i = 0; i < RELAY_COUNT; i++) digitalWrite(RELAY_PINS[i], v ? HIGH : LOW);
  } else {
    if (sscanf(arg, "%d %d", &n, &v) != 2 || n < 1 || n > RELAY_COUNT) { err("usage: r<n> <0|1>, n=1..4"); return; }
    digitalWrite(RELAY_PINS[n - 1], v ? HIGH : LOW);
  }
  printBits("REL: ", readRelays(), RELAY_COUNT); ok();
}

static void cmdWalk() {
  for (uint8_t i = 0; i < RELAY_COUNT; i++) {
    digitalWrite(RELAY_PINS[i], HIGH); Serial.print("REL"); Serial.print(i + 1); Serial.println(" on"); delay(300);
    digitalWrite(RELAY_PINS[i], LOW);  delay(150);
  }
  printBits("REL: ", readRelays(), RELAY_COUNT); ok();
}

static void cmdLed(char *arg) {
  while (*arg == ' ') arg++;
  if (strncmp(arg, "auto", 4) == 0) { ledAuto = true; ok(); return; }
  if (*arg == '0' || *arg == '1') { ledAuto = false; digitalWrite(PIN_LED, *arg == '1'); ok(); return; }
  err("usage: l <0|1|auto>");
}

static void cmdScan() {
  Serial.print("I2C Wire/J10: ");
  uint8_t found = 0;
  for (uint8_t a = 0x08; a < 0x78; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) { Serial.print("0x"); Serial.print(a, HEX); Serial.print(' '); found++; }
  }
  if (!found) Serial.print("(none)");
  Serial.println();
  ok();
}

// ---- OLED on the Grove connector J10 (Wire, PA08/PA09)
static bool oledBegin() {
  if (oledOk) return true;
  Wire.beginTransmission(0x3C);                 // Adafruit begin() "succeeds" without a display
  if (Wire.endTransmission() != 0) return false;
  oledOk = oled.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  if (oledOk) { oled.setTextColor(SSD1306_WHITE); oled.setTextWrap(true); }
  return oledOk;
}

static void oledStatus() {
  oled.clearDisplay();
  oled.setTextSize(1); oled.setCursor(0, 0);
  oled.println("ProDINo MKR Zero ETH");
  oled.print("fw "); oled.print(FW_VERSION); oled.print("  up "); oled.print(millis() / 1000); oled.println("s");
  oled.drawLine(0, 18, 127, 18, SSD1306_WHITE);
  oled.setCursor(0, 22); oled.print("REL ");
  for (uint8_t i = 0; i < RELAY_COUNT; i++) oled.print((readRelays() >> i) & 1);
  oled.print("   IN ");
  for (uint8_t i = 0; i < INPUT_COUNT; i++) oled.print((readInputs() >> i) & 1);
  oled.setCursor(0, 34); oled.print("RS485 "); if (rs485Up) oled.print(rs485Baud); else oled.print("off");
  oled.setCursor(0, 46); oled.print("ETH "); oled.print(!ethOk ? "-" : (ethRead8(W5500_PHYCFGR) & 1 ? "link" : "nolink"));
  oled.setCursor(0, 56); oled.print("J10 OLED 0x3C ok");
  oled.display();
}

static void cmdOled(char *arg) {
  while (*arg == ' ') arg++;
  if (!oledBegin()) { err("no SSD1306 at 0x3C on J10"); return; }
  if (strncmp(arg, "off", 3) == 0) { oled.clearDisplay(); oled.display(); ok(); return; }
  if (*arg) {                                   // free text, large font
    oled.clearDisplay(); oled.setTextSize(2); oled.setCursor(0, 0); oled.print(arg); oled.display();
    Serial.print("OLED: "); Serial.println(arg); ok(); return;
  }
  // test pattern: full white, then checkerboard, then status page
  oled.clearDisplay(); oled.fillRect(0, 0, 128, 64, SSD1306_WHITE); oled.display(); delay(300);
  oled.clearDisplay();
  for (int16_t y = 0; y < 64; y += 8) for (int16_t x = ((y / 8) & 1) * 8; x < 128; x += 16) oled.fillRect(x, y, 8, 8, SSD1306_WHITE);
  oled.display(); delay(300);
  oledStatus();
  Serial.println("OLED 128x64 @0x3C: pattern + status shown");
  ok();
}

// W5500 on the MKR SPI (SERCOM1: MOSI PA16 / SCK PA17 / MISO PA19).
static const uint8_t ETH_MAC[6] = { 0x02, 0x4D, 0x49, 0x56, 0x10, 0x01 };   // locally administered "MIV"
static bool ethStarted = false;

static void ethBegin() {
  if (ethStarted) return;
  pinMode(PIN_ETH_CS, OUTPUT); digitalWrite(PIN_ETH_CS, HIGH);
  pinMode(PIN_ETH_RST, OUTPUT); digitalWrite(PIN_ETH_RST, LOW); delay(2); digitalWrite(PIN_ETH_RST, HIGH); delay(100);
  SPI.begin();
  Ethernet.init(PIN_ETH_CS);
  ethStarted = true;
}

// Raw common-register access (W5500 frame: addr16, control, data), used by the probe.
static void ethXfer(uint16_t addr, bool write, uint8_t *buf, size_t n) {
  SPI.beginTransaction(SPISettings(8000000, MSBFIRST, SPI_MODE0));
  digitalWrite(PIN_ETH_CS, LOW);
  SPI.transfer(addr >> 8); SPI.transfer(addr & 0xFF); SPI.transfer(write ? 0x04 : 0x00);
  for (size_t i = 0; i < n; i++) { uint8_t v = SPI.transfer(write ? buf[i] : 0x00); if (!write) buf[i] = v; }
  digitalWrite(PIN_ETH_CS, HIGH);
  SPI.endTransaction();
}
static uint8_t ethRead8(uint16_t addr) { uint8_t v = 0; ethXfer(addr, false, &v, 1); return v; }

static void cmdEth() {
  ethBegin();
  uint8_t ver = ethRead8(W5500_VERSIONR);
  Serial.print("W5500 VERSIONR=0x"); hexByte(ver); Serial.println(ver == 0x04 ? " (ok)" : " (expected 0x04)");
  if (ver != 0x04) { ethOk = false; err("W5500 not responding on SPI"); return; }
  ethOk = true;
  uint8_t rb[6] = {0};                                     // MAC write + read back exercises MOSI and MISO
  ethXfer(W5500_SHAR, true, (uint8_t *)ETH_MAC, 6);
  ethXfer(W5500_SHAR, false, rb, 6);
  bool same = memcmp(ETH_MAC, rb, 6) == 0;
  Serial.print("SHAR r/w: ");
  for (uint8_t i = 0; i < 6; i++) { hexByte(rb[i]); Serial.print(i < 5 ? ':' : ' '); }
  Serial.println(same ? "(match)" : "(MISMATCH)");
  uint8_t phy = ethRead8(W5500_PHYCFGR);
  for (uint32_t t0 = millis(); !(phy & 1) && millis() - t0 < 3500; ) { delay(100); phy = ethRead8(W5500_PHYCFGR); }  // PHY auto-neg after reset takes ~2-3 s
  Serial.print("PHYCFGR=0x"); hexByte(phy);
  Serial.print("  link="); Serial.print(phy & 0x01 ? "UP" : "down");
  Serial.print(" speed="); Serial.print(phy & 0x02 ? "100M" : "10M");
  Serial.print(" duplex="); Serial.println(phy & 0x04 ? "full" : "half");
  Serial.print("INTn="); Serial.println(digitalRead(PIN_ETH_INT));
  if (same) ok(); else err("SPI data mismatch");
}

static void cmdEthDhcp() {
  ethBegin();
  Serial.println("DHCP...");
  int r = Ethernet.begin((uint8_t *)ETH_MAC, 8000, 2000);
  if (Ethernet.hardwareStatus() == EthernetNoHardware) { err("no W5500 on SPI"); return; }
  if (Ethernet.linkStatus() == LinkOFF) { err("link down - cable?"); return; }
  if (!r) { err("DHCP failed"); return; }
  Serial.print("IP="); Serial.print(Ethernet.localIP());
  Serial.print(" GW="); Serial.print(Ethernet.gatewayIP());
  Serial.print(" DNS="); Serial.println(Ethernet.dnsServerIP());
  ethOk = true; ok();
}

// ---- RS485: Serial1 (SERCOM5, TX PB22 / RX PB23) + ST3485, DE and /RE tied to D3.
// While transmitting the receiver is off, so the board never hears its own frame:
// a real test needs a second node (the tester's USB-RS485 adapter).
static void rs485Rx() {
  static uint8_t buf[64];
  static uint8_t n = 0;
  static uint32_t tLast = 0;
  while (Serial1.available() && n < sizeof(buf)) { buf[n++] = Serial1.read(); tLast = millis(); }
  if (n && (n == sizeof(buf) || millis() - tLast > 30)) {   // 30 ms gap closes a frame
    Serial.print("EV 485RX len="); Serial.print(n); Serial.print(" data=");
    for (uint8_t i = 0; i < n; i++) { hexByte(buf[i]); Serial.print(' '); }
    Serial.print("text=\"");
    for (uint8_t i = 0; i < n; i++) Serial.print(buf[i] >= 32 && buf[i] < 127 ? (char)buf[i] : '.');
    Serial.println('"');
    n = 0;
  }
}

static void cmdRs485(char *arg) {
  while (*arg == ' ') arg++;
  if (strncmp(arg, "init", 4) == 0) {
    long baud = 9600; sscanf(arg + 4, "%ld", &baud); rs485Baud = baud;
    pinMode(PIN_485_DE, OUTPUT); digitalWrite(PIN_485_DE, LOW);   // receive
    Serial1.begin(baud);
    rs485Up = true;
    Serial.print("RS485 up at "); Serial.print(baud); Serial.println(" 8N1"); ok(); return;
  }
  if (strncmp(arg, "off", 3) == 0) {
    if (rs485Up) Serial1.end();
    rs485Up = false; digitalWrite(PIN_485_DE, LOW); ok(); return;
  }
  if (!rs485Up) { err("RS485 not initialised - x init"); return; }
  if (strncmp(arg, "send", 4) == 0) {
    char *p = arg + 4; while (*p == ' ') p++;
    digitalWrite(PIN_485_DE, HIGH); delayMicroseconds(50);
    size_t n = Serial1.print(p); n += Serial1.print("\r\n");
    Serial1.flush();                                 // waits for the last stop bit (TXC)
    delayMicroseconds(50); digitalWrite(PIN_485_DE, LOW);
    Serial.print("485TX len="); Serial.println(n); ok(); return;
  }
  err("x init [baud]|off|send <text>");
}

// ---- add-on header J14
static void cmdGpio(char *arg) {
  int j; char op;
  if (sscanf(arg, "%d %c", &j, &op) != 2 || j < 4 || j > 11) { err("usage: g <J14 pin 4..11> <0|1|i|r>"); return; }
  uint8_t pin = J14_PINS[j];
  switch (op) {
    case '0': pinMode(pin, OUTPUT); digitalWrite(pin, LOW);  break;
    case '1': pinMode(pin, OUTPUT); digitalWrite(pin, HIGH); break;
    case 'i': pinMode(pin, INPUT_PULLUP); break;
    case 'r': break;
    default: err("op 0|1|i|r"); return;
  }
  Serial.print("J14."); Serial.print(j); Serial.print('='); Serial.println(digitalRead(pin)); ok();
}

// Serial2 (SERCOM4: TX PA12 J14.7, RX PA13 J14.6, RTS PA14 J14.5, CTS PA15 J14.4) with
// hardware flow control; the loopback plug ties TX->RX and RTS->CTS. Never flush():
// without the plug CTS floats and the transmitter could stall.
static void cmdUartLoop() {
  Serial2.begin(115200);
  while (Serial2.available()) Serial2.read();
  const char probe[] = "MIV-J14";
  Serial2.write((const uint8_t *)probe, sizeof(probe) - 1);
  char rx[16] = {0}; uint8_t n = 0;
  for (uint32_t t0 = millis(); millis() - t0 < 200 && n < sizeof(probe) - 1; )
    if (Serial2.available()) rx[n++] = Serial2.read();
  Serial2.end();
  for (uint8_t j = 4; j <= 7; j++) pinMode(J14_PINS[j], INPUT);   // give the pins back
  Serial.print("J14 UART loopback: sent \""); Serial.print(probe); Serial.print("\" got \""); Serial.print(rx); Serial.println('"');
  if (n == sizeof(probe) - 1 && !memcmp(rx, probe, n)) ok(); else err("no echo - jumpers J14.4-5 and J14.6-7 plugged?");
}

static void resetIntoBootloader() {
  // Arduino SAM-BA bootloader: magic in the last RAM word = "double tap", stay in bootloader
  *(volatile uint32_t *)(HMCRAMC0_ADDR + HMCRAMC0_SIZE - 4) = 0x07738135UL;
  NVIC_SystemReset();
}

static void help() {
  Serial.println("? v st r r<n> t i l s d e x g u reset boot   (see source header for arguments)");
  ok();
}

static void handleLine(char *line) {
  while (*line == ' ') line++;
  size_t n = strlen(line); while (n && (line[n - 1] == ' ' || line[n - 1] == '\r')) line[--n] = 0;
  if (!n) return;
  if (!strcmp(line, "?") || !strcmp(line, "help")) { help(); return; }
  if (!strcmp(line, "v"))     { cmdInfo(); return; }
  if (!strcmp(line, "st"))    { cmdStatus(); return; }
  if (!strcmp(line, "i"))     { printBits("IN: ", readInputs(), INPUT_COUNT); ok(); return; }
  if (!strcmp(line, "t"))     { cmdWalk(); return; }
  if (!strcmp(line, "s"))     { cmdScan(); return; }
  if (!strcmp(line, "u"))     { cmdUartLoop(); return; }
  if (line[0] == 'd' && (line[1] == 0 || line[1] == ' ')) { cmdOled(line + 1); return; }
  if (!strcmp(line, "e"))     { cmdEth(); return; }
  if (!strcmp(line, "e dhcp")){ cmdEthDhcp(); return; }
  if (!strcmp(line, "e link")){ ethBegin(); Serial.println(Ethernet.linkStatus() == LinkON ? "link UP" : "link down"); ok(); return; }
  if (!strcmp(line, "reset")) { ok(); Serial.flush(); delay(50); NVIC_SystemReset(); }
  if (!strcmp(line, "boot"))  { ok(); Serial.flush(); delay(50); resetIntoBootloader(); }
  switch (line[0]) {
    case 'r': cmdRelay(line + 1); return;
    case 'l': cmdLed(line + 1);   return;
    case 'x': cmdRs485(line + 1); return;
    case 'g': cmdGpio(line + 1);  return;
  }
  err("unknown command, ? for help");
}

// ---------------------------------------------------------------- arduino
void setup() {
  pinMode(PIN_LED, OUTPUT);
  for (uint8_t i = 0; i < RELAY_COUNT; i++) { pinMode(RELAY_PINS[i], OUTPUT); digitalWrite(RELAY_PINS[i], LOW); }
  for (uint8_t i = 0; i < INPUT_COUNT; i++) pinMode(INPUT_PINS[i], INPUT);   // external pull-ups on board
  pinMode(PIN_ETH_INT, INPUT_PULLUP);
  pinMode(PIN_485_DE, OUTPUT); digitalWrite(PIN_485_DE, LOW);

  Wire.begin();

  Serial.begin(115200);
  uint32_t t0 = millis();
  while (!Serial && millis() - t0 < 3000) {}
  Serial.println();
  Serial.println("EV BOOT ProDINo MKR Zero Ethernet fw " FW_VERSION);
  lastInputs = readInputs();
  if (oledBegin()) oledStatus();
}

void loop() {
  static char     line[80];
  static uint8_t  len = 0;
  static uint32_t tLed = 0;
  uint32_t now = millis();

  if (ledAuto && now - tLed >= 500) { tLed = now; digitalWrite(PIN_LED, !digitalRead(PIN_LED)); }

  uint8_t in = readInputs();
  if (in != lastInputs) { lastInputs = in; printBits("EV IN ", in, INPUT_COUNT); }

  if (rs485Up) rs485Rx();

  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r' || c == '\n') { line[len] = 0; if (len) handleLine(line); len = 0; }
    else if (len < sizeof(line) - 1) line[len++] = c;
  }
}

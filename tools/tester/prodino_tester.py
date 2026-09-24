"""
ProDINo MKR Zero Ethernet - Tester

Single-window desktop tool (tkinter) for the production / bring-up flow:
  1. Flash the application over USB through the installed (KMP / Arduino SAM-BA)
     bootloader: 1200 bps touch or console "boot", then the bundled bossac
  2. Exercise every board function through the firmware's console protocol
     (relays, inputs, LED, Ethernet, RS485, I2C/OLED on J10, add-on header J14)
  3. Raw terminal for manual commands

Packaged with PyInstaller (see build_exe.ps1). Resources (bossac.exe, firmware bin)
are bundled but the firmware can be overridden with "Browse...".
"""
from __future__ import annotations

import ctypes
import glob
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import serial
import serial.tools.list_ports

APP_TITLE = "ProDINo MKR Zero Ethernet - Tester"
VID = 0x2341
PID_APP = 0x8052          # Arduino MKR GSM 1400 IDs (the board is pin compatible)
PID_BOOT = 0x0052


# ----------------------------------------------------------------------------- resources
def res_path(name: str) -> str:
    """Path of a bundled resource (PyInstaller) or the file next to the script."""
    base = getattr(sys, "_MEIPASS", os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources"))
    return os.path.join(base, name)


def find_resource(pattern: str) -> str:
    hits = glob.glob(res_path(pattern))
    return hits[0] if hits else ""


def find_port(pid: int) -> str:
    for p in serial.tools.list_ports.comports():
        if p.vid == VID and p.pid == pid:
            return p.device
    return ""


# ----------------------------------------------------------------------------- serial link
class Link:
    """Background reader; lines go to a queue for the GUI, to a waiter for command replies
    and (EV lines) to an event list that tests can wait on."""

    def __init__(self, on_line):
        self.ser: serial.Serial | None = None
        self.on_line = on_line
        self._lock = threading.Lock()
        self._reply: list[str] = []
        self._waiting = False
        self._done = threading.Event()
        self.events: list[str] = []

    @property
    def open(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def connect(self, port: str):
        self.ser = serial.Serial(port, 115200, timeout=0.1)
        threading.Thread(target=self._reader, daemon=True).start()

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    def _reader(self):
        buf = b""
        while self.open:
            try:
                chunk = self.ser.read(256)
            except Exception:
                self.on_line("!! port lost")
                self.ser = None
                break
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", "replace").rstrip("\r")
                self.on_line(line)
                with self._lock:
                    if line.startswith("EV "):
                        self.events.append(line)
                        del self.events[:-50]
                    elif self._waiting:
                        self._reply.append(line)
                        if line == "OK" or line.startswith("ERR"):
                            self._done.set()

    def send(self, cmd: str):
        if not self.open:
            raise RuntimeError("not connected")
        self.ser.write((cmd + "\n").encode())

    def command(self, cmd: str, timeout: float = 3.0) -> tuple[bool, list[str]]:
        """Send and wait for OK/ERR. Returns (ok, lines without the OK/ERR line)."""
        with self._lock:
            self._reply = []
            self._waiting = True
            self._done.clear()
        self.send(cmd)
        got = self._done.wait(timeout)
        with self._lock:
            self._waiting = False
            lines = list(self._reply)
        if not got:
            return False, lines + ["(timeout)"]
        status = lines.pop()
        return status == "OK", lines + ([status] if status != "OK" else [])

    def wait_event(self, prefix: str, contains: str, timeout: float) -> str:
        t0 = time.time()
        while time.time() - t0 < timeout:
            with self._lock:
                for e in self.events:
                    if e.startswith(prefix) and contains in e:
                        return e
            time.sleep(0.05)
        return ""


# ----------------------------------------------------------------------------- GUI
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        # HiDPI: scale points->pixels for the real DPI, then size the window in scaled units
        scale = self.winfo_fpixels("1i") / 72.0
        self.tk.call("tk", "scaling", scale)
        px = lambda v: int(v * scale / (96 / 72))
        self.geometry(f"{px(1180)}x{px(780)}")
        self.minsize(px(980), px(620))
        self.q: queue.Queue[str] = queue.Queue()
        self.link = Link(self.q.put)
        self.busy = False
        self.show_st = False           # status polls are hidden in the terminal unless the user typed "st"
        self._hide_ok = False
        self.inputs = [0, 0, 0, 0]
        self.relays = [0, 0, 0, 0]

        self._build()
        self.after(100, self._pump)
        self.after(1000, self._autodetect)
        self.after(700, self._poll_status)

    # ------------------------------------------------------------------ layout
    def _build(self):
        top = ttk.Frame(self, padding=6)
        top.pack(fill="x")
        ttk.Label(top, text="Port:").pack(side="left")
        self.port_var = tk.StringVar()
        self.port_cb = ttk.Combobox(top, textvariable=self.port_var, width=12, state="readonly")
        self.port_cb.pack(side="left", padx=4)
        ttk.Button(top, text="Refresh", command=self._refresh_ports).pack(side="left")
        self.conn_btn = ttk.Button(top, text="Connect", command=self._toggle_connect)
        self.conn_btn.pack(side="left", padx=6)
        self.state_lbl = ttk.Label(top, text="● disconnected", foreground="gray")
        self.state_lbl.pack(side="left", padx=10)
        self.status_lbl = ttk.Label(top, text="")
        self.status_lbl.pack(side="right")

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=6, pady=4)

        left = ttk.Frame(body, width=520)
        body.add(left, weight=0)
        nb = ttk.Notebook(left)
        nb.pack(fill="both", expand=True)
        nb.add(self._tab_flash(nb), text=" Flash ")
        nb.add(self._tab_io(nb), text=" I/O ")
        nb.add(self._tab_periph(nb), text=" Peripherals ")
        nb.add(self._tab_rs485(nb), text=" RS485 ")
        nb.add(self._tab_all(nb), text=" Run all ")

        right = ttk.Frame(body)
        body.add(right, weight=1)
        ttk.Label(right, text="Terminal").pack(anchor="w")
        self.term = tk.Text(right, wrap="none", font=("Consolas", 10), bg="#101418", fg="#d8dee9",
                            insertbackground="white", height=20)
        self.term.pack(fill="both", expand=True)
        self.term.tag_config("tx", foreground="#8fbcbb")
        self.term.tag_config("ev", foreground="#ebcb8b")
        self.term.tag_config("err", foreground="#bf616a")
        self.term.tag_config("ok", foreground="#a3be8c")
        self.term.tag_config("sys", foreground="#81a1c1")
        line = ttk.Frame(right)
        line.pack(fill="x", pady=3)
        self.cmd_var = tk.StringVar()
        ent = ttk.Entry(line, textvariable=self.cmd_var)
        ent.pack(side="left", fill="x", expand=True)
        ent.bind("<Return>", lambda e: self._send_manual())
        ttk.Button(line, text="Send", command=self._send_manual).pack(side="left", padx=4)
        ttk.Button(line, text="Clear", command=lambda: self.term.delete("1.0", "end")).pack(side="left")

    def _tab_flash(self, parent):
        f = ttk.Frame(parent, padding=8)
        lf = ttk.LabelFrame(f, text="Application (USB, bootloader already on the board)", padding=6)
        lf.pack(fill="x", pady=4)
        self.fw_var = tk.StringVar(value=find_resource("firmware*.bin"))
        self.bossac_var = tk.StringVar(value=find_resource("bossac.exe"))
        self._file_row(lf, "firmware .bin", self.fw_var, [("bin", "*.bin")])
        self._file_row(lf, "bossac.exe", self.bossac_var, [("bossac", "bossac.exe")])
        r = ttk.Frame(lf)
        r.pack(fill="x", pady=2)
        ttk.Button(r, text="Flash application", command=self._flash_app).pack(side="left")
        ttk.Button(r, text="Enter bootloader", command=lambda: self._bg(self._enter_bootloader)).pack(side="left", padx=6)
        ttk.Button(r, text="Reset board", command=lambda: self._do("reset")).pack(side="left")
        self.boot_lbl = ttk.Label(lf, text="")
        self.boot_lbl.pack(anchor="w")
        ttk.Label(f, text="The board shows up as \"Arduino MKR GSM 1400\" (USB 2341:8052, bootloader 2341:0052).\n"
                          "Bootloader entry by hand: double-tap RESET (B1); the LED fades in and out.\n"
                          "Blank chip: the bootloader goes in over SWD (J-Link on J11) - see README.",
                  foreground="gray").pack(anchor="w", pady=6)
        return f

    def _file_row(self, parent, label, var, types):
        r = ttk.Frame(parent)
        r.pack(fill="x", pady=1)
        ttk.Label(r, text=label, width=15).pack(side="left")
        ttk.Entry(r, textvariable=var, width=34).pack(side="left")
        ttk.Button(r, text="Browse...", width=9,
                   command=lambda: var.set(filedialog.askopenfilename(filetypes=types) or var.get())).pack(side="left", padx=3)

    def _tab_io(self, parent):
        f = ttk.Frame(parent, padding=8)
        lf = ttk.LabelFrame(f, text="Relays (J2: REL1-2, J3: REL3-4)", padding=6)
        lf.pack(fill="x", pady=4)
        self.relay_btns = []
        for i in range(4):
            b = tk.Button(lf, text=f"REL{i + 1}\nOFF", width=8, height=2, bg="#ddd",
                          command=lambda i=i: self._do(f"r{i + 1} {0 if self.relays[i] else 1}"))
            b.grid(row=0, column=i, padx=4, pady=2)
            self.relay_btns.append(b)
        ttk.Button(lf, text="All ON", command=lambda: self._do("r all 1")).grid(row=1, column=0, pady=4)
        ttk.Button(lf, text="All OFF", command=lambda: self._do("r all 0")).grid(row=1, column=1)
        ttk.Button(lf, text="Walk test", command=lambda: self._do("t", 4)).grid(row=1, column=2)

        lf2 = ttk.LabelFrame(f, text="Opto inputs (J9) - 1 = energised", padding=6)
        lf2.pack(fill="x", pady=4)
        self.in_lbls = []
        for i in range(4):
            l = tk.Label(lf2, text=f"IN{i + 1}\n0", width=8, height=2, bg="#ddd", relief="groove")
            l.grid(row=0, column=i, padx=4, pady=2)
            self.in_lbls.append(l)

        lf3 = ttk.LabelFrame(f, text="Status LED", padding=6)
        lf3.pack(fill="x", pady=4)
        for txt, c in (("LED on", "l 1"), ("LED off", "l 0"), ("LED blink", "l auto")):
            ttk.Button(lf3, text=txt, command=lambda c=c: self._do(c)).pack(side="left", padx=2)

        lf4 = ttk.LabelFrame(f, text="Add-on header J14", padding=6)
        lf4.pack(fill="x", pady=4)
        ttk.Label(lf4, text="Pin:").grid(row=0, column=0, sticky="w")
        pins = ["4 CTS", "5 RTS", "6 RX", "7 TX", "8 DTR", "9 RESET", "10 BOOT0", "11 RF_NRST"]
        self.gpio_var = tk.StringVar(value=pins[0])
        ttk.Combobox(lf4, textvariable=self.gpio_var, values=pins, width=12, state="readonly").grid(row=0, column=1, padx=4)
        for j, (txt, op) in enumerate((("Set 0", "0"), ("Set 1", "1"), ("Input+PU", "i"), ("Read", "r"))):
            ttk.Button(lf4, text=txt, width=9,
                       command=lambda op=op: self._do(f"g {self.gpio_var.get().split()[0]} {op}")).grid(row=1, column=j, padx=2, pady=2)
        ttk.Button(lf4, text="UART loopback (jumpers 4-5, 6-7)", command=lambda: self._do("u")).grid(
            row=2, column=0, columnspan=4, sticky="w", pady=2)
        return f

    def _tab_periph(self, parent):
        f = ttk.Frame(parent, padding=8)
        lf2 = ttk.LabelFrame(f, text="Ethernet W5500 (SPI)", padding=6)
        lf2.pack(fill="x", pady=4)
        r1 = ttk.Frame(lf2); r1.pack(fill="x", pady=1)
        ttk.Button(r1, text="Probe (version, PHY link)", command=lambda: self._do("e", 5)).pack(side="left", padx=2)
        ttk.Button(r1, text="DHCP", command=lambda: self._do("e dhcp", 12)).pack(side="left", padx=2)
        ttk.Button(r1, text="Link", command=lambda: self._do("e link", 3)).pack(side="left", padx=2)
        ttk.Label(lf2, text="Plug a cable for link / DHCP.", foreground="gray").pack(anchor="w", pady=2)

        lfo = ttk.LabelFrame(f, text="Grove J10 (I2C) - OLED SSD1306 128x64 @0x3C", padding=6)
        lfo.pack(fill="x", pady=4)
        ttk.Button(lfo, text="I2C scan", command=lambda: self._do("s", 3)).pack(side="left", padx=2)
        ttk.Button(lfo, text="OLED pattern", command=lambda: self._do("d", 4)).pack(side="left", padx=2)
        ttk.Button(lfo, text="Clear", command=lambda: self._do("d off")).pack(side="left", padx=2)
        self.oled_text = tk.StringVar(value="MIV OK")
        ttk.Entry(lfo, textvariable=self.oled_text, width=12).pack(side="left", padx=6)
        ttk.Button(lfo, text="Show", command=lambda: self._do("d " + self.oled_text.get())).pack(side="left")

        lf3 = ttk.LabelFrame(f, text="Board", padding=6)
        lf3.pack(fill="x", pady=4)
        ttk.Button(lf3, text="Info (v)", command=lambda: self._do("v")).pack(side="left", padx=2)
        ttk.Button(lf3, text="Status (st)", command=lambda: self._do("st")).pack(side="left", padx=2)
        ttk.Button(lf3, text="Help (?)", command=lambda: self._do("?")).pack(side="left", padx=2)
        return f

    def _tab_rs485(self, parent):
        f = ttk.Frame(parent, padding=8)
        lf = ttk.LabelFrame(f, text="Board side (J6: A / B)", padding=6)
        lf.pack(fill="x", pady=4)
        ttk.Label(lf, text="Baud:").pack(side="left")
        self.baud_var = tk.StringVar(value="9600")
        ttk.Combobox(lf, textvariable=self.baud_var, values=["9600", "19200", "38400", "57600", "115200"],
                     width=7).pack(side="left", padx=4)
        ttk.Button(lf, text="Init", command=lambda: self._do("x init " + self.baud_var.get())).pack(side="left", padx=2)
        ttk.Button(lf, text="Off", command=lambda: self._do("x off")).pack(side="left", padx=2)
        r2 = ttk.Frame(f)
        r2.pack(fill="x", pady=4)
        self.x_text = tk.StringVar(value="hello from ProDINo")
        ttk.Entry(r2, textvariable=self.x_text, width=30).pack(side="left", padx=2)
        ttk.Button(r2, text="Board sends", command=lambda: self._do("x send " + self.x_text.get())).pack(side="left", padx=2)

        lf2 = ttk.LabelFrame(f, text="PC side: USB-RS485 adapter wired to J6 (optional)", padding=6)
        lf2.pack(fill="x", pady=8)
        ttk.Label(lf2, text="Adapter port:").grid(row=0, column=0, sticky="w")
        self.adapter_var = tk.StringVar()
        self.adapter_cb = ttk.Combobox(lf2, textvariable=self.adapter_var, width=12, state="readonly",
                                       postcommand=lambda: self.adapter_cb.configure(
                                           values=[p.device for p in serial.tools.list_ports.comports()
                                                   if p.device != self.port_var.get()]))
        self.adapter_cb.grid(row=0, column=1, padx=4)
        ttk.Button(lf2, text="Two-way test", command=lambda: self._bg(self._rs485_pair_job)).grid(row=0, column=2, padx=4)
        ttk.Label(f, text="The ST3485 has DE and /RE tied together: the board never hears its own frame,\n"
                          "so a real TX+RX check needs a second node. Frames the board receives appear\n"
                          "in the terminal as 'EV 485RX ...'. A -> A, B -> B.",
                  foreground="gray").pack(anchor="w", pady=6)
        return f

    def _tab_all(self, parent):
        f = ttk.Frame(parent, padding=8)
        ttk.Label(f, text="Runs every automatic check and prints a PASS/FAIL report.\n"
                          "Relays click; inputs are only reported (they need external signals).\n"
                          "Tick what the test jig has - unticked checks are skipped, not failed.").pack(anchor="w")
        opts = ttk.Frame(f)
        opts.pack(anchor="w", pady=4)
        self.opt_cable = tk.BooleanVar(value=True)
        self.opt_oled = tk.BooleanVar(value=False)
        self.opt_485 = tk.BooleanVar(value=False)
        self.opt_j14 = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Ethernet cable (link + DHCP)", variable=self.opt_cable).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(opts, text="OLED on J10", variable=self.opt_oled).grid(row=0, column=1, sticky="w", padx=10)
        ttk.Checkbutton(opts, text="USB-RS485 adapter (RS485 tab)", variable=self.opt_485).grid(row=1, column=0, sticky="w")
        ttk.Checkbutton(opts, text="J14 loopback plug", variable=self.opt_j14).grid(row=1, column=1, sticky="w", padx=10)
        ttk.Button(f, text="Run all tests", command=lambda: self._bg(self._run_all)).pack(anchor="w", pady=6)
        self.report = tk.Text(f, height=18, width=48, font=("Consolas", 10))
        self.report.pack(fill="both", expand=True)
        self.report.tag_config("pass", foreground="green")
        self.report.tag_config("fail", foreground="red")
        self.report.tag_config("skip", foreground="gray")
        self.report.tag_config("big_pass", foreground="white", background="#2e8b57", font=("Consolas", 14, "bold"))
        self.report.tag_config("big_fail", foreground="white", background="#b22222", font=("Consolas", 14, "bold"))
        return f

    # ------------------------------------------------------------------ terminal / link
    def log(self, text: str, tag: str = ""):
        self.term.insert("end", text + "\n", tag)
        self.term.see("end")

    def _pump(self):
        try:
            while True:
                line = self.q.get_nowait()
                self._parse_line(line)
                if line.startswith("ST ") and not self.show_st:     # silent status poll
                    self._hide_ok = True
                    continue
                if self._hide_ok and line == "OK":
                    self._hide_ok = False
                    continue
                if line.startswith("ST "):
                    self.show_st = False
                tag = ("ok" if line.startswith("PASS ") else "err" if line.startswith("FAIL ") else
                       "ev" if line.startswith("EV ") else
                       "err" if line.startswith("ERR") or line.startswith("!!") else
                       "ok" if line == "OK" else
                       "sys" if line.startswith("$") or line.startswith("  ") else "")
                self.log(line, tag)
        except queue.Empty:
            pass
        if not self.link.open and self.conn_btn["text"] == "Disconnect":
            self._set_connected(False)
        self.after(60, self._pump)

    def _parse_line(self, line: str):
        if line.startswith("ST "):
            kv = dict(p.split("=", 1) for p in line[3:].split() if "=" in p)
            if "rel" in kv:
                self.relays = [int(c) for c in kv["rel"]]
            if "in" in kv:
                self.inputs = [int(c) for c in kv["in"]]
            self.status_lbl["text"] = (f"up {int(kv.get('up', 0)) // 1000}s   ETH {kv.get('eth', '-')}   "
                                       f"RS485 {kv.get('rs485', '-')}   OLED {kv.get('oled', '-')}")
        elif line.startswith("EV IN "):
            self.inputs = [int(c) for c in line[6:].split()]
        elif line.startswith("IN: "):
            self.inputs = [int(c) for c in line[4:].split()]
        elif line.startswith("REL: "):
            self.relays = [int(c) for c in line[5:].split()]
        else:
            return
        for i in range(4):
            self.relay_btns[i].config(text=f"REL{i + 1}\n{'ON' if self.relays[i] else 'OFF'}",
                                      bg="#8fd18f" if self.relays[i] else "#ddd")
            self.in_lbls[i].config(text=f"IN{i + 1}\n{self.inputs[i]}", bg="#ebcb8b" if self.inputs[i] else "#ddd")

    def _refresh_ports(self):
        ports = [f"{p.device}" for p in serial.tools.list_ports.comports()]
        self.port_cb["values"] = ports
        app = find_port(PID_APP)
        if app:
            self.port_var.set(app)
        elif ports and self.port_var.get() not in ports:
            self.port_var.set(ports[0])
        boot = find_port(PID_BOOT)
        self.boot_lbl["text"] = f"Bootloader port present: {boot}" if boot else ""

    def _autodetect(self):
        if not self.link.open and not self.busy:
            self._refresh_ports()
            if find_port(PID_APP):
                self._toggle_connect()
        self.after(2000, self._autodetect)

    def _toggle_connect(self):
        if self.link.open:
            self.link.close()
            self._set_connected(False)
            return
        port = self.port_var.get()
        if not port:
            return
        try:
            self.link.connect(port)
        except Exception as e:
            self.log(f"!! cannot open {port}: {e}", "err")
            return
        self._set_connected(True, port)
        self.after(300, lambda: self._do("v"))

    def _set_connected(self, on: bool, port: str = ""):
        self.conn_btn["text"] = "Disconnect" if on else "Connect"
        self.state_lbl.config(text=f"● connected {port}" if on else "● disconnected", foreground="green" if on else "gray")
        if not on:
            self.status_lbl["text"] = ""

    def _poll_status(self):
        if self.link.open and not self.busy:
            try:
                self.link.send("st")
            except Exception:
                pass
        self.after(500, self._poll_status)

    def _send_manual(self):
        cmd = self.cmd_var.get().strip()
        if cmd:
            self._do(cmd)
            self.cmd_var.set("")

    def _do(self, cmd: str, timeout: float = 3.0):
        """Fire a console command from a button (reply shows up in the terminal)."""
        if not self.link.open:
            self.log("!! not connected", "err")
            return
        self.log("> " + cmd, "tx")
        if cmd.strip() == "st":
            self.show_st = True
        try:
            self.link.send(cmd)
        except Exception as e:
            self.log(f"!! {e}", "err")

    # ------------------------------------------------------------------ background jobs
    def _bg(self, fn):
        if self.busy:
            self.log("!! busy", "err")
            return

        def run():
            self.busy = True
            try:
                fn()
            except Exception as e:
                self.q.put(f"!! {e}")
            finally:
                self.busy = False
        threading.Thread(target=run, daemon=True).start()

    def _run_cmdline(self, args: list[str], cwd: str | None = None) -> int:
        self.q.put("$ " + " ".join(args))
        p = subprocess.Popen(args, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for line in p.stdout:
            line = line.rstrip()
            if line and not line.startswith("["):          # skip the progress bars
                self.q.put("  " + line)
        return p.wait()

    def _enter_bootloader(self) -> str:
        """Ask the app to reboot into the bootloader (console 'boot', else 1200 bps touch). Returns its port."""
        port = find_port(PID_BOOT)
        if port:
            return port
        if self.link.open:
            try:
                self.link.send("boot")
            except Exception:
                pass
            time.sleep(0.2)
            self.link.close()
            self.q.put("!! sent 'boot', waiting for the bootloader port")
        else:
            app = find_port(PID_APP)
            if app:
                self.q.put(f"!! 1200 bps touch on {app}")
                try:
                    serial.Serial(app, 1200).close()
                except Exception as e:
                    self.q.put(f"!! touch failed: {e}")
        t0 = time.time()
        while time.time() - t0 < 10 and not port:
            time.sleep(0.3)
            port = find_port(PID_BOOT)
        self.q.put(f"!! bootloader port: {port}" if port else "!! no bootloader port - double-tap RESET (B1)")
        return port

    def _flash_app(self):
        fw, bossac = self.fw_var.get(), self.bossac_var.get()
        if not os.path.exists(fw):
            messagebox.showerror(APP_TITLE, "firmware file not found")
            return
        if not os.path.exists(bossac):
            messagebox.showerror(APP_TITLE, "bossac.exe not found")
            return

        def job():
            port = self._enter_bootloader()
            if not port:
                raise RuntimeError("cannot reach the bootloader")
            time.sleep(0.5)
            # Arduino bossac 1.7: the SAMD21 SAM-BA bootloader maps the write to 0x2000 itself
            rc = self._run_cmdline([bossac, "--info", f"--port={port}", "--write", "--verify", "--reset",
                                    "--erase", "-U", "true", fw])
            if rc != 0:
                raise RuntimeError(f"bossac exited with {rc}")
            t0 = time.time()
            while time.time() - t0 < 10 and not find_port(PID_APP):
                time.sleep(0.3)
            self.q.put("!! application is up" if find_port(PID_APP) else "!! application port not seen yet")
        self._bg(job)

    # ------------------------------------------------------------------ RS485 with the PC adapter
    def _rs485_pair(self, rep: list | None = None) -> bool:
        """Board -> adapter and adapter -> board at the selected baud. Returns overall ok."""
        aport, baud = self.adapter_var.get(), int(self.baud_var.get())
        if not aport:
            raise RuntimeError("pick the USB-RS485 adapter port on the RS485 tab")
        results = []
        ok, lines = self.link.command(f"x init {baud}")
        if not ok:
            results.append(("RS485 init", False, " | ".join(lines)))
        else:
            with serial.Serial(aport, baud, timeout=0.1) as ad:
                ad.reset_input_buffer()
                ok, lines = self.link.command("x send MIV-BOARD")
                data, t0 = b"", time.time()
                while time.time() - t0 < 0.8 and b"MIV-BOARD" not in data:
                    data += ad.read(64)
                got = b"MIV-BOARD" in data
                results.append(("RS485 board -> PC", ok and got, f"adapter got {data[:40]!r}"))
                self.q.put(f"!! adapter received {data!r}")
                with self.link._lock:
                    self.link.events.clear()
                ad.write(b"MIV-PC\r\n")
                ev = self.link.wait_event("EV 485RX", "MIV-PC", 1.0)
                results.append(("RS485 PC -> board", bool(ev), ev or "no EV 485RX"))
        for r in results:
            self.q.put(f"{'PASS' if r[1] else 'FAIL'} {r[0]}: {r[2]}")
        if rep is not None:
            rep.extend(results)
        return all(r[1] for r in results)

    def _rs485_pair_job(self):
        if not self.link.open:
            raise RuntimeError("not connected")
        self._rs485_pair()

    # ------------------------------------------------------------------ run all
    def _run_all(self):
        if not self.link.open:
            raise RuntimeError("not connected")
        rep = []           # (name, True/False/None=skip, detail)
        time.sleep(0.7)    # let a pending status poll finish before we start collecting replies

        def check(name, cmd, timeout=3.0, must=None):
            ok, lines = self.link.command(cmd, timeout)
            if ok and must:
                ok = any(must in l for l in lines)
            rep.append((name, ok, " | ".join(lines)[:120]))
            return ok, lines

        def skip(name, why="not ticked"):
            rep.append((name, None, why))

        self.report.delete("1.0", "end")
        check("USB console / info", "v", must="fw:")
        check("W5500 SPI (VERSIONR, MAC r/w)", "e", 5, must="(match)")
        if self.opt_cable.get():
            link_ok, _ = check("Ethernet link (cable)", "e link", 3, must="link UP")
            if link_ok:
                check("Ethernet DHCP (IP)", "e dhcp", 14, must="IP=")
            else:
                rep.append(("Ethernet DHCP (IP)", False, "no link"))
        else:
            skip("Ethernet link (cable)"); skip("Ethernet DHCP (IP)")
        check("Relays: all ON", "r all 1", must="1 1 1 1")
        time.sleep(0.4)
        check("Relays: all OFF", "r all 0", must="0 0 0 0")
        check("Relay walk", "t", 5)
        check("RS485 init", "x init 9600", 3)
        check("RS485 send", "x send MIV-TEST", 3, must="485TX")
        if self.opt_485.get():
            try:
                self._rs485_pair(rep)
            except Exception as e:
                rep.append(("RS485 two-way (adapter)", False, str(e)))
        else:
            skip("RS485 two-way (adapter)")
        if self.opt_oled.get():
            check("I2C: OLED on J10 (0x3C)", "s", 3, must="0x3C")
            check("OLED test pattern + status", "d", 5, must="pattern")
        else:
            skip("I2C / OLED on J10")
        if self.opt_j14.get():
            check("J14 UART loopback (Serial2)", "u", 3, must="got \"MIV-J14\"")
        else:
            skip("J14 UART loopback")
        check("LED blink", "l auto")
        check("Inputs (info only)", "i")

        passed = sum(1 for _, o, _ in rep if o is True)
        failed = sum(1 for _, o, _ in rep if o is False)
        skipped = sum(1 for _, o, _ in rep if o is None)
        self.report.insert("end", f"{'TEST':34s} RESULT\n" + "-" * 44 + "\n")
        for name, o, detail in rep:
            word, tag = ("PASS", "pass") if o is True else ("FAIL", "fail") if o is False else ("skip", "skip")
            self.report.insert("end", f"{name:34s} {word}\n", tag)
            if o is False or name.startswith("Ethernet DHCP"):
                self.report.insert("end", f"    {detail[:60]}\n")
        total = passed + failed
        self.report.insert("end", "-" * 44 + f"\n{passed}/{total} passed" + (f", {skipped} skipped" if skipped else "") + "\n")
        verdict = "PASS" if failed == 0 else "FAIL"
        self.report.insert("end", f"\n  {verdict}: {passed}/{total}  \n", "big_pass" if verdict == "PASS" else "big_fail")
        self.q.put(f"{verdict} run all: {passed}/{total} passed, {skipped} skipped")


if __name__ == "__main__":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # crisp text on HiDPI screens
    except Exception:
        pass
    App().mainloop()

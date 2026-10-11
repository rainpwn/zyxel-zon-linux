#!/usr/bin/env python3
"""zon-gui.py - graphical front end for zon.py. It runs as your user and starts "zon.py --serve" as root
through pkexec, so the system asks for your password once per interface.

  ./zon-gui.py [iface]

Needs Tkinter (Debian/Ubuntu: python3-tk, Fedora: python3-tkinter) and pkexec (polkit).
"""
import json, os, queue, re, shlex, subprocess, sys, threading, urllib.request, webbrowser
import tkinter as tk
from tkinter import font, ttk

from zon import __version__

ZON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zon.py")
REPO = "https://github.com/rainpwn/zyxel-zon-linux"
COFFEE = "https://buymeacoffee.com/rainpwn"
COLS = [("Device Model", "Model", 150), ("IPv4 Address", "IP address", 120), ("System Name", "Name", 150),
        ("FW Version", "Firmware", 210), ("MAC", "MAC", 140)]
EDIT = [(7, "IPv4 Address"), (8, "IPv4 Netmask"), (9, "IPv4 Gateway"), (10, "IPv4 DNS Server 1"),
        (22, "System Name"), (24, "Location")]
AUTH = {"get", "set", "save", "locate", "reboot", "renew", "factory_reset", "passwd"}
CONFIRMED = {"set", "reboot", "factory_reset"}  # zon.py asks "[yes/no]" first; the GUI confirms on its own
C = dict(bg="#f3f4f6", surface="#ffffff", line="#d8dce2", ink="#1d2329", muted="#5b6672", off="#a3acb6",
         accent="#1565c0", accent_hover="#0f4f99", accent_off="#a7bfe0", select="#dce8f8", hover="#eceff3",
         press="#e1e5ea", stripe="#f8f9fb", danger="#b42318", danger_hover="#fbeae8")


def interfaces():
    out = []
    for n in sorted(os.listdir("/sys/class/net")):
        try:
            if open(f"/sys/class/net/{n}/type").read().strip() == "1":  # Ethernet only, like zon.py
                out.append(n)
        except OSError:
            pass
    return out


def latest_release():
    """Tag of the latest GitHub release, e.g. "v0.2.0", or None when GitHub cannot be reached."""
    try:
        api = REPO.replace("github.com", "api.github.com/repos") + "/releases/latest"
        with urllib.request.urlopen(api, timeout=5) as r:
            return json.load(r)["tag_name"]
    except (OSError, ValueError, KeyError):
        return None


def newer(tag):
    v = lambda s: tuple(int(x) for x in re.findall(r"\d+", s)[:3])
    return v(tag) > v(__version__)


def theme(root, px):
    base = font.nametofont("TkDefaultFont")
    bold = base.copy(); bold.configure(weight="bold")
    title = base.copy(); title.configure(size=int(base.cget("size") * 1.35), weight="bold")
    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=C["bg"], foreground=C["ink"], bordercolor=C["line"], lightcolor=C["bg"],
                darkcolor=C["bg"], troughcolor=C["bg"], focuscolor=C["accent"], fieldbackground=C["surface"],
                selectbackground=C["select"], selectforeground=C["ink"], insertcolor=C["ink"])
    s.configure("Panel.TFrame", background=C["surface"])
    s.configure("Panel.TLabel", background=C["surface"])
    s.configure("Title.TLabel", background=C["surface"], font=title)
    s.configure("Muted.TLabel", foreground=C["muted"])
    s.configure("PanelMuted.TLabel", background=C["surface"], foreground=C["muted"])
    s.configure("Group.TLabel", background=C["surface"], foreground=C["muted"], font=bold)
    link = base.copy(); link.configure(underline=True)
    s.configure("Link.TLabel", foreground=C["accent"], font=link)
    s.configure("PanelLink.TLabel", background=C["surface"], foreground=C["accent"], font=link)
    s.configure("Empty.TLabel", background=C["surface"], foreground=C["muted"], justify="center")
    s.configure("TSeparator", background=C["line"])

    for name, bg, fg, hover, press, off_bg, off_fg in [
            ("TButton", C["surface"], C["ink"], C["hover"], C["press"], C["surface"], C["off"]),
            ("Accent.TButton", C["accent"], "#ffffff", C["accent_hover"], C["accent_hover"], C["accent_off"], "#ffffff"),
            ("Danger.TButton", C["surface"], C["danger"], C["danger_hover"], C["danger_hover"], C["surface"], C["off"])]:
        s.configure(name, background=bg, foreground=fg, lightcolor=bg, darkcolor=bg, padding=(px(12), px(6)),
                    bordercolor=C["accent"] if name == "Accent.TButton" else C["line"], focuscolor=bg)
        state = [("disabled", off_bg), ("pressed", press), ("active", hover)]
        s.map(name, background=state, lightcolor=state, darkcolor=state, foreground=[("disabled", off_fg)],
              bordercolor=[("focus", C["ink"] if name == "Accent.TButton" else C["accent"])])
    for name in ("Action.TButton", "ActionDanger.TButton"):  # full-width rows in the side panel
        s.configure(name, anchor="w", padding=(px(10), px(5)), bordercolor=C["surface"])
        s.map(name, bordercolor=[("focus", C["accent"])])

    s.configure("TEntry", padding=px(6), bordercolor=C["line"], lightcolor=C["surface"], darkcolor=C["surface"])
    s.map("TEntry", bordercolor=[("focus", C["accent"])], lightcolor=[("focus", C["accent"])])
    s.configure("TCombobox", padding=px(5), arrowcolor=C["muted"], background=C["surface"],
                lightcolor=C["surface"], darkcolor=C["surface"])
    s.map("TCombobox", bordercolor=[("focus", C["accent"])], fieldbackground=[("readonly", C["surface"])],
          background=[("active", C["hover"])])
    root.option_add("*TCombobox*Listbox.selectBackground", C["select"])
    root.option_add("*TCombobox*Listbox.selectForeground", C["ink"])

    s.configure("Treeview", background=C["surface"], fieldbackground=C["surface"], borderwidth=0,
                rowheight=base.metrics("linespace") + px(12))
    s.map("Treeview", background=[("selected", C["select"])], foreground=[("selected", C["ink"])])
    s.configure("Treeview.Heading", background=C["bg"], foreground=C["muted"], font=bold, relief="flat",
                padding=(px(8), px(6)), lightcolor=C["bg"], darkcolor=C["bg"], bordercolor=C["line"])
    s.map("Treeview.Heading", background=[("active", C["hover"])])
    s.configure("TScrollbar", background=C["line"], bordercolor=C["bg"], lightcolor=C["line"],
                darkcolor=C["line"], arrowcolor=C["muted"], troughcolor=C["bg"], gripcount=0)
    s.map("TScrollbar", background=[("active", C["off"])])
    s.configure("TProgressbar", background=C["accent"], lightcolor=C["accent"], darkcolor=C["accent"],
                troughcolor=C["line"], bordercolor=C["line"], thickness=px(4))
    return bold, title, link  # the caller keeps them: Tk deletes a named font when its Python object goes away


class App:
    def __init__(self, root, iface):
        self.root, self.proc, self.proc_iface, self.devs, self.auth = root, None, None, {}, []
        self.replies, self.images = queue.Queue(), []
        scale = float(root.tk.call("tk", "scaling")) / (96 / 72)  # 1.0 at 96 dpi
        self.px = lambda n: max(1, round(n * scale))
        self.icon_size = 36 if scale >= 1.5 else 18
        px = self.px
        self.fonts = theme(root, px)
        root.title(f"ZON for Linux {__version__}")
        root.configure(background=C["bg"])
        root.geometry(f"{px(1080)}x{px(640)}")
        root.minsize(px(860), px(600))
        root.iconphoto(True, tk.PhotoImage(data=ICONS["app@64"]))

        # toolbar
        bar = ttk.Frame(root, padding=(px(14), px(12), px(14), px(10)))
        bar.pack(fill="x")
        ttk.Label(bar, image=self.icon("iface")[0]).pack(side="left")
        ttk.Label(bar, text="Interface").pack(side="left", padx=(px(6), px(8)))
        self.iface = ttk.Combobox(bar, values=interfaces(), state="readonly", width=14)
        self.iface.pack(side="left")
        if iface or self.iface["values"]:
            self.iface.set(iface or self.iface["values"][0])
        self.discover_btn = self.button(bar, "Discover", "discover", lambda: self.send("discover"), "Accent.TButton")
        self.discover_btn.pack(side="left", padx=(px(10), 0))
        root.bind("<F5>", lambda e: self.discover_btn.instate(["!disabled"]) and self.send("discover"))
        self.button(bar, "About", "about", self.about, "TButton").pack(side="right")
        self.update_link = ttk.Label(bar, style="Link.TLabel", cursor="hand2")
        self.update_link.bind("<Button-1>", lambda e: webbrowser.open(REPO + "/releases/latest"))
        self.status = ttk.Label(bar, style="Muted.TLabel")
        self.status.pack(side="right", padx=(0, px(14)))
        self.spinner = ttk.Progressbar(bar, mode="indeterminate", length=px(90))

        body = ttk.Frame(root, padding=(px(14), 0, px(14), px(14)))
        body.pack(fill="both", expand=True)

        # side panel with the actions for the selected device
        panel = ttk.Frame(body, style="Panel.TFrame", padding=px(14), width=px(250))
        panel.pack(side="right", fill="y", padx=(px(12), 0))
        panel.pack_propagate(False)
        self.dev_title = ttk.Label(panel, style="Title.TLabel", text="No device selected")
        self.dev_title.pack(anchor="w")
        self.dev_sub = ttk.Label(panel, style="PanelMuted.TLabel", text="Select a device in the list.")
        self.dev_sub.pack(anchor="w", pady=(px(2), px(6)))
        self.actions = []
        for group, items in [
                ("Device", [("Read all fields", "read", lambda: self.run("get")),
                            ("Edit settings", "edit", self.edit),
                            ("Blink locator LED", "locate", lambda: self.run("locate"))]),
                ("Maintenance", [("Save configuration", "save", lambda: self.run("save")),
                                 ("Renew DHCP lease", "renew", lambda: self.run("renew")),
                                 ("Change password", "passwd", self.passwd)]),
                ("Power", [("Reboot", "reboot", self.reboot), ("Factory reset", "factory_reset", self.factory_reset)])]:
            ttk.Separator(panel).pack(fill="x", pady=(px(8), px(6)))
            ttk.Label(panel, text=group, style="Group.TLabel").pack(anchor="w", pady=(0, px(4)))
            for text, key, fn in items:
                b = self.button(panel, text, key, fn, "ActionDanger.TButton" if group == "Power" else "Action.TButton")
                b.pack(fill="x", pady=1)
                self.actions.append(b)

        # device list over the output pane
        panes = ttk.PanedWindow(body, orient="vertical")
        panes.pack(side="left", fill="both", expand=True)
        listing = ttk.Frame(panes, style="Panel.TFrame")
        self.tree = ttk.Treeview(listing, columns=[c for c, *_ in COLS], show="headings", selectmode="browse")
        for c, title, width in COLS:
            self.tree.heading(c, text=title, anchor="w")
            self.tree.column(c, width=px(width), minwidth=px(80), stretch=c == "FW Version")
        self.tree.tag_configure("stripe", background=C["stripe"])
        sb = ttk.Scrollbar(listing, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.select())
        self.empty = ttk.Label(listing, style="Empty.TLabel",
                               text="No devices yet.\nChoose the interface on the devices' LAN and press Discover.")
        self.empty.place(relx=0.5, rely=0.55, anchor="center")
        self.text = tk.Text(panes, font="TkFixedFont", height=10, wrap="none", state="disabled", relief="flat",
                            background=C["surface"], foreground=C["ink"], selectbackground=C["select"],
                            selectforeground=C["ink"], inactiveselectbackground=C["select"],
                            highlightthickness=0, padx=px(12), pady=px(10))
        panes.add(listing, weight=3)
        panes.add(self.text, weight=2)
        root.protocol("WM_DELETE_WINDOW", self.quit)
        self.select()
        self.latest = None
        self.check_update()

    def icon(self, key):
        """(normal, disabled) images, with room on the right so the label does not touch the icon."""
        pair = []
        for tag in ("", "_off"):
            data = ICONS.get(f"{key}{tag}@{self.icon_size}")
            if data is None:
                pair.append(pair[0]); continue
            src = tk.PhotoImage(data=data)
            img = tk.PhotoImage(width=src.width() + self.px(8), height=src.height())
            img.tk.call(img, "copy", src)
            self.images += [src, img]
            pair.append(img)
        return pair

    def button(self, parent, text, key, fn, style):
        on, off = self.icon(key)
        return ttk.Button(parent, text=text, image=(on, "disabled", off), compound="left", command=fn, style=style)

    # ---- backend ----

    def start(self):
        iface = self.iface.get()
        if self.proc and self.proc.poll() is None and self.proc_iface == iface:
            return True
        self.stop()
        if not iface:
            self.show("No Ethernet interface found. ZDP needs a wired or wireless LAN interface.")
            return False
        cmd = [sys.executable, ZON, iface, "--serve"]
        if os.geteuid():
            cmd.insert(0, "pkexec")
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, text=True)
        except FileNotFoundError:
            self.show("pkexec not found. Install polkit, or run zon.py from a terminal with sudo.")
            return False
        self.proc_iface, self.devs, self.auth = iface, {}, []
        return True

    def stop(self):
        if self.proc:
            self.proc.stdin.close()  # zon.py --serve exits at end of input
            self.proc = None

    def quit(self):
        self.stop()
        self.root.destroy()

    def busy(self, on, msg=""):
        for w in [self.discover_btn, *self.actions]:
            w.state(["disabled"] if on else ["!disabled"])
        self.iface.state(["disabled"] if on else ["!disabled", "readonly"])
        self.root.config(cursor="watch" if on else "")
        if on:
            self.spinner.pack(side="right", padx=(0, self.px(10)))
            self.spinner.start(12)
        else:
            self.spinner.stop()
            self.spinner.pack_forget()
            self.select()
        self.status.config(text=msg)

    def send(self, line, answers=()):
        if not self.start():
            return
        proc, verb = self.proc, line.split()[0]
        first = not self.devs and verb == "discover"
        self.busy(True, f"Searching {self.proc_iface}..." if verb == "discover" else "Waiting for the device...")
        if first and os.geteuid():
            self.show("Waiting for authorization: zon.py needs root to send raw Ethernet frames.")

        def work():
            try:
                proc.stdin.write(json.dumps({"cmd": line, "answers": list(answers)}) + "\n")
                proc.stdin.flush()
                reply = proc.stdout.readline()
                self.replies.put(json.loads(reply) if reply else {"exit": proc.stderr.read().strip()})
            except (OSError, ValueError) as e:
                self.replies.put({"exit": str(e)})
        threading.Thread(target=work, daemon=True).start()
        self.root.after(100, self.poll, verb)

    def poll(self, verb):
        try:
            r = self.replies.get_nowait()
        except queue.Empty:
            return self.root.after(100, self.poll, verb)
        if "exit" in r:
            self.proc = None
            self.busy(False, "Stopped")
            return self.show("zon.py stopped. " + (r["exit"] or "Authorization was refused or cancelled."))
        self.devs, self.auth = r["devs"], r["auth"]
        if list(self.tree.get_children()) != list(self.devs):  # rebuild only on change: keeps the selection
            self.tree.delete(*self.tree.get_children())
            for i, mac in enumerate(self.devs):
                self.tree.insert("", "end", iid=mac, tags=("stripe",) if i % 2 else ())
        for mac, f in self.devs.items():
            self.tree.item(mac, values=[mac if c == "MAC" else f.get(c, "") for c, *_ in COLS])
        if self.devs:
            self.empty.place_forget()
        else:
            self.empty.config(text=f"No Zyxel device answered on {self.proc_iface}.\n"
                                   "ZDP only reaches devices on the same LAN or VLAN.")
            self.empty.place(relx=0.5, rely=0.55, anchor="center")
        n = len(self.devs)
        self.busy(False, f"{n} device{'s' * (n != 1)} on {self.proc_iface}")
        if verb == "discover":
            self.show(f"Found {n} device{'s' * (n != 1)}. Select one to see every field it reported.")
        else:
            self.show(r["output"].strip() or "Done.")

    # ---- updates ----

    def check_update(self, done=None):
        """Ask GitHub for the latest release in the background; done() runs on the Tk thread afterwards."""
        q = queue.Queue()
        threading.Thread(target=lambda: q.put(latest_release()), daemon=True).start()

        def wait():
            try:
                self.latest = q.get_nowait()
            except queue.Empty:
                return self.root.after(200, wait)
            if self.latest and newer(self.latest):
                self.update_link.config(text=f"Update available: {self.latest}")
                self.update_link.pack(side="right", padx=(0, self.px(14)), before=self.status)
            if done:
                done()
        wait()

    def update_text(self):
        if self.latest is None:
            return "Could not reach GitHub to check for updates."
        if newer(self.latest):
            return f"Version {self.latest} is available."
        return "You have the latest version."

    # ---- dialogs ----

    def dialog(self, title):
        win = tk.Toplevel(self.root, background=C["bg"])
        win.title(title)
        win.transient(self.root)
        win.resizable(False, False)
        win.bind("<Escape>", lambda e: win.destroy())
        frm = ttk.Frame(win, padding=self.px(18))
        frm.pack(fill="both", expand=True)
        return win, frm

    def modal(self, win, focus):
        """Center win on the main window and keep the input until it closes."""
        win.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - win.winfo_reqheight()) // 3
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        win.wait_visibility()
        win.grab_set()
        focus.focus_set()
        self.root.wait_window(win)

    def about(self):
        px = self.px
        win, frm = self.dialog("About ZON for Linux")
        card = ttk.Frame(frm, style="Panel.TFrame", padding=px(16))
        card.pack(fill="x")
        logo = tk.PhotoImage(data=ICONS["app@64"])
        self.images.append(logo)
        ttk.Label(card, image=logo, style="Panel.TLabel").grid(row=0, column=0, rowspan=2, padx=(0, px(14)))
        ttk.Label(card, text="ZON for Linux", style="Title.TLabel").grid(row=0, column=1, sticky="sw")
        ttk.Label(card, text=f"Version {__version__}", style="PanelMuted.TLabel").grid(row=1, column=1, sticky="nw")
        ttk.Label(frm, wraplength=px(400), justify="left",
                  text="Discover and manage Zyxel switches, access points and firewalls from Linux over ZDP, "
                       "the layer 2 protocol of Zyxel One Network Utility.").pack(anchor="w", pady=(px(14), 0))

        row = ttk.Frame(frm)
        row.pack(fill="x", pady=(px(14), 0))
        status = ttk.Label(row, text=self.update_text(), style="Muted.TLabel")
        status.pack(side="left")
        check = ttk.Button(row, text="Check for updates")
        check.pack(side="right", padx=(px(12), 0))

        def checked():
            if status.winfo_exists():
                status.config(text=self.update_text())
                check.state(["!disabled"])

        def recheck():
            status.config(text="Checking...")
            check.state(["disabled"])
            self.check_update(checked)
        check.config(command=recheck)

        ttk.Separator(frm).pack(fill="x", pady=px(14))
        for line in ["Made by Alessandro Sgreccia (rainpwn). MIT License.",
                     "Icons: Lucide (lucide.dev), ISC License.",
                     "Independent project, not affiliated with or endorsed by Zyxel."]:
            ttk.Label(frm, text=line, style="Muted.TLabel").pack(anchor="w")
        links = ttk.Frame(frm, style="Panel.TFrame", padding=(px(12), px(8)))
        links.pack(fill="x", pady=(px(14), 0))
        for i, (text, url) in enumerate([("Source code", REPO), ("Report a problem", REPO + "/issues"),
                                         ("Buy me a coffee", COFFEE)]):
            lbl = ttk.Label(links, text=text, style="PanelLink.TLabel", cursor="hand2")
            lbl.bind("<Button-1>", lambda e, url=url: webbrowser.open(url))
            lbl.pack(side="left", padx=(0 if i == 0 else px(16), 0))
        close = ttk.Button(frm, text="Close", command=win.destroy, style="Accent.TButton")
        close.pack(anchor="e", pady=(px(16), 0))
        win.bind("<Return>", lambda e: win.destroy())
        self.modal(win, close)

    def form(self, title, intro, fields=(), ok="Apply", danger=False):
        """Modal dialog. fields: [(key, label, initial, secret)]. Returns {key: value}, or None if cancelled."""
        px, result = self.px, {}
        win, frm = self.dialog(title)
        ttk.Label(frm, text=intro, wraplength=px(380), justify="left").grid(row=0, column=0, columnspan=2,
                                                                          sticky="w", pady=(0, px(12)))
        entries = {}
        for i, (key, label, initial, secret) in enumerate(fields, 1):
            ttk.Label(frm, text=label, style="Muted.TLabel").grid(row=i, column=0, sticky="w", pady=px(3))
            e = ttk.Entry(frm, width=30, show="•" if secret else "")
            e.insert(0, initial)
            e.grid(row=i, column=1, sticky="ew", padx=(px(12), 0), pady=px(3))
            entries[key] = e

        def accept(*_):
            result.update({k: e.get() for k, e in entries.items()})
            result["_ok"] = True
            win.destroy()
        btns = ttk.Frame(frm)
        btns.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="e", pady=(px(16), 0))
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="left")
        ttk.Button(btns, text=ok, command=accept, style="Danger.TButton" if danger else "Accent.TButton").pack(
            side="left", padx=(px(8), 0))
        if not danger:  # Enter must never trigger a reboot or a reset by reflex
            win.bind("<Return>", accept)
        self.modal(win, next(iter(entries.values())) if entries else win)
        return result if result.pop("_ok", False) else None

    # ---- actions ----

    def selected(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def select(self):
        mac = self.selected()
        for b in self.actions:
            b.state(["!disabled"] if mac else ["disabled"])
        if not mac:
            self.dev_title.config(text="No device selected")
            self.dev_sub.config(text="Select a device in the list.")
            return
        d = self.devs[mac]
        self.dev_title.config(text=d.get("Device Model", "") or mac)
        self.dev_sub.config(text=f"{d.get('IPv4 Address', '')}  ·  {mac}")
        self.show(mac + "\n" + "\n".join(f"  {k:38} {v}" for k, v in d.items()))

    def name(self, mac):
        return f"{self.devs[mac].get('Device Model', '')} ({mac})"

    def run(self, verb, *args, extra=(), password=None):
        mac = self.selected()
        if not mac:
            return
        answers = ["yes"] if verb in CONFIRMED else []
        if verb in AUTH and mac not in self.auth:
            if password is None:
                r = self.form("Device password", f"Admin password for {self.name(mac)}. It is kept in memory "
                              "until you close the program.", [("pw", "Password", "", True)], ok="Continue")
                password = r and r["pw"]
            if not password:
                return
            answers.append(password)
        self.send(shlex.join([verb, mac, *args]), answers + list(extra))

    def edit(self):
        mac = self.selected()
        d = self.devs[mac]
        r = self.form("Edit settings", f"Settings of {self.name(mac)}. Only the fields you change are sent.",
                      [(t, name, str(d.get(name, "")), False) for t, name in EDIT], ok="Apply")
        if r:
            pairs = [(t, v.strip()) for t, v in r.items() if v.strip() != str(d.get(dict(EDIT)[t], ""))]
            if pairs:
                self.run("set", *[x for t, v in pairs for x in (str(t), v)])

    def passwd(self):
        mac = self.selected()
        fields = [] if mac in self.auth else [("old", "Current password", "", True)]
        r = self.form("Change password", f"New admin password for {self.name(mac)}, 4 to 200 characters.",
                      fields + [("new", "New password", "", True), ("again", "Repeat", "", True)], ok="Change")
        if not r:
            return
        if r["new"] != r["again"] or not 4 <= len(r["new"]) <= 200:
            return self.show("Password not changed: the two entries differ or the length is outside 4-200.")
        self.run("passwd", extra=[r["new"], r["new"]], password=r.get("old"))

    def reboot(self):
        mac = self.selected()
        if self.form("Reboot", f"Reboot {self.name(mac)}? It drops off the network until it has restarted.",
                     ok="Reboot", danger=True) is not None:
            self.run("reboot")

    def factory_reset(self):
        mac = self.selected()
        if self.form("Factory reset", f"Reset {self.name(mac)} to factory defaults? Every setting is lost, "
                     "including its IP address and admin password.", ok="Factory reset", danger=True) is not None:
            self.run("factory_reset")

    def show(self, text):
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)
        self.text.config(state="disabled")


def main():
    root = tk.Tk()
    App(root, sys.argv[1] if len(sys.argv) > 1 else None)
    root.mainloop()


# Icons: Lucide (lucide.dev, ISC license), rendered to PNG with 16 alpha levels; key@pixels, _off = disabled.
ICONS = {
    'discover@18': 'iVBORw0KGgoAAAANSUhEUgAAABIAAAASBAMAAACk4JNkAAAAMFBMVEX///////////////////////////////////////////////////////////////9Or7hAAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAK1JREFUeNo9zq0KwlAAgNFvd3NDcDgxWETGBJPgwK4DBzbfwaxBm81qHJhMewRfwaTFMMzi/KuKzOKEOYNgPuXIgOh8HgCUwjQZAzBdN4eJDVhFqrRPCBZ3fx87PmCVY9d/ZRxF/cy75+NM82j042z6ktRImLt3bfKuV4QAACvPTz1fjdBXS8dS4lyA9tSDgh60R0gHw97K0s2A1pUCvQsghxt3kNgA2n+FcE3gC3aVOBHueQ+3AAAAAElFTkSuQmCC',
    'discover@36': 'iVBORw0KGgoAAAANSUhEUgAAACQAAAAkBAMAAAATLoWrAAAAMFBMVEX///////////////////////////////////////////////////////////////9Or7hAAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAZlJREFUeNp1ksFLFHEUxz/zm1HTFAeCCLFlCKSDCHMQPES41iHoYgVew05Rl0kEj9o1QcbbdglWMPAi66VrK/YHtHkKRMa8VuwmrY6r49fD7Kzo1ru8x3vf9/j9vt8vtIXdzENTD7uiS4MlSfruX3RMSZKkhg9gAfQd8Gv9z+gD4u4MdVufXLAeSyutxdk039NJWnROAzdezk3AV3kABIdwK5KSmW4dk7U6qpJ0WtJyazFQsrYYKQMBjpKncFNa5v5W+rKctoBrOoYwxuHD/tsZPefRyDAFqDhgtGdV6wwovZSTZwwV293hI1AAfuMbw55h08k3ihTtXe8M11jUDDWbwmvyHXeeCNe0s+oI9wwv4VU/FUPNooajDUcVSlKDPvnXlcfohxXVGZBWCBJy8iD6S5h4jM1iqkdMJkDYIKdtgEF9oxQD4/KdlM2y8rYOgB5tEpzQOU25Tq+KgFGM/YLgkLs+Ycr0gt406aZLRwD0KM5ECbWR/qCsLy3RXJowrV6Vdl76+f7dZzUvtdvkH2ayMss90+46/4tzf4nKMwV7eIAAAAAASUVORK5CYII=',
    'discover_off@18': 'iVBORw0KGgoAAAANSUhEUgAAABIAAAASBAMAAACk4JNkAAAAMFBMVEWjrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLaXNI5fAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAK1JREFUeNo9zq0KwlAAgNFvd7ohONgsFgWZYBIc2N3Agc13MGvQZrMaBZNpvoGvYNJiGGZx/lVRZtkGU4NgPuXIgGi/nwAUg086AmCyaQ5SCzALVLHPCBYP7xA7HmCWYteLsk5Gec87l9NUXdLoxblPJCmhqOyT2jipl4UAAFPnpzNPCdHWK8fMxHkf9aX5hubbQ6Sjbu1k6a5D64ZB9wrIwdbtpxaA+l8h3ArwBX1xOCGJ0YvGAAAAAElFTkSuQmCC',
    'discover_off@36': 'iVBORw0KGgoAAAANSUhEUgAAACQAAAAkBAMAAAATLoWrAAAAMFBMVEWjrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLaXNI5fAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAZZJREFUeNp1ksFLFHEUxz/zm1lNTRwIIiSXQQoPIcwh6BDRVoegQ2UgXr3qZVwEj+Y1QcbbdglWKOgiK3RvxX+g7RaEzOa1xE1cHddmvx5mdiVW3+X9+P6+7/He9/ugJ+ws35l+1h/997EmST/8C8RUJElq+QAWwNgefzb/3n9KPNBhjemjC9YLaaNbuJjmhzpLH32zwI2ZpSfwTR4AwTHciqSkOKBTOlAukqR/Fa13CwMln1cjdUiAo2QKbkrrPNpJJ8trB7imUwhjHD7srRTbL3k+eY8S1HJgVLcOmowq7ZSXZww12/3JJ6AE7OMbQ91m2ym0ypTtXa+NaywaFnWb0jyF3Phr4ZpeVR3htvES5kaoGRoWDXLaclSjIrUYlj+kAka/rKjJqLRBkJCXB9ERYeLyYBFzcMKrBAhb2ULc1ncqMfBYvpOqWVXB1iEwqG2CM/pmqTa5rjJgFOMUCY6Z8AlTpd9qIZObfp0AMKi4Y0qorXSDqr50TXPJaL3WLku/37/7qqxT75lcckxWBt59o91NropzygLJw8ea5m0AAAAASUVORK5CYII=',
    'read@18': 'iVBORw0KGgoAAAANSUhEUgAAABIAAAASBAMAAACk4JNkAAAAMFBMVEU7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlJ0E5tEAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAFtJREFUeNpjYMABBKE0y9r/txwYGBgYmPa/7tj3W4GBgcHumwADY/5jBgbGtwqc/ycw3RNg4PrEMH/mTwb9BQz2BQw/GeYzsH9mQpiFkEXoQJiCZDLCNiQXYAAAMEUlZoozCIIAAAAASUVORK5CYII=',
    'read@36': 'iVBORw0KGgoAAAANSUhEUgAAACQAAAAkBAMAAAATLoWrAAAAMFBMVEU7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlJ0E5tEAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAO1JREFUeNq9kT1OAnEQRx9bsBia1cbGGGy0M/Zaa+kBbIgnWI09nkC9gRcwQmKsia0F3ECjNUpjAshmn8UibMi/1SlfZia/D/ifqawuge27l4/n0zI5UdXHBTlQ80/14ZfEOr3YWju813SG2k4aANGV3wWpO92DSgJR185s6RLi3iCFWrFWdQxRX7MGXNsEdr2BDfOhT7DiO3CbAS3PdxwB/QlEfgH9EbQy4NgGsR2ovL7BZp5A3TQqhCRle4HDwPuQiKqjhdSWzbmh9d7gbG4oZJu246VwqBURHnUXEbKv5sNy0KE6QqWFqv2r+QHnDZlGYSJ2ngAAAABJRU5ErkJggg==',
    'read_off@18': 'iVBORw0KGgoAAAANSUhEUgAAABIAAAASBAMAAACk4JNkAAAAMFBMVEWjrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLaXNI5fAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAFtJREFUeNpjYMABBKE0y9r/txwYGBgYmPa/7tj3W4GBgcHumwADY/5jBgbGtwqc/ycw3RNg4PrEMH/mTwb9BQz2BQw/GeYzsH9mQpiFkEXoQJiCZDLCNiQXYAAAMEUlZoozCIIAAAAASUVORK5CYII=',
    'read_off@36': 'iVBORw0KGgoAAAANSUhEUgAAACQAAAAkBAMAAAATLoWrAAAAMFBMVEWjrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLaXNI5fAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAPFJREFUeNq9kb1OwlAYhp/WpNWwNC4uxtRFN8OOu6MXwEK8gmrcyx3gHXADJJAYZkLcHNo7gMiMYTGWn8rrcFog5IRNv/HJd77z/sD/jBPsgavO6PP9YZfUJUnqb8mtpPVM0ltJfGn1fHl6N5CiAnW1CAHctpaGVLSqmh+PBuoVS03wk2kEx2bN0xzcVMpDaKkB3OgFzrWeaQgnmgDtHIj1dK0MSBfg6gtIM4hz4F4hvnrgjD/g4ieAiiLXCAkAnEKn5eHO+e/ivE2Ep2wrNVZjY+gsmT5uDNls09XchNMqw7FFSK0M+vVQHbbSbNX+1fwCS3+ZeYuPV00AAAAASUVORK5CYII=',
    'edit@18': 'iVBORw0KGgoAAAANSUhEUgAAABIAAAASBAMAAACk4JNkAAAAMFBMVEU7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlJ0E5tEAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAHdJREFUeNpjYGBgYGBgYHJkgIL6fwEQBsdPn4cQVv8E5i8QoX+XOT8xMjAwMPR/ydp0kYGBgYHjB4PeDwYmBgaG9g6GxA6oEMcPiMYGhv4GHELMP6FCTKw/GNo7GBgYGBh4NmhCVDEpy1yDCDHY/WmGuozZAOpGAJvFImxc+q0xAAAAAElFTkSuQmCC',
    'edit@36': 'iVBORw0KGgoAAAANSUhEUgAAACQAAAAkBAMAAAATLoWrAAAAMFBMVEU7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlJ0E5tEAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAOxJREFUeNqF0rFKA0EQxvG/l4sSSZEmRTo72zxBzCNYW1pb+AZaCXYHQq4MKbRNJXkUS0UfYLlGWHPsZ3FnsrOnON1+82MYloFO9cxrfNV/s/0jp+2JjZaS3k0yUH3v6gQVzDQ1yMNA19k+WvACspPkYajzeNKrCpZhtEfBT4Kf6DNCrmhhhH7GxQg6qNmMvN2pWgELyhQFz061qFr9jhrVtJ866LhF5Dv0+Dcih7uDMkE9WUROn02CGGpuEHAWRrB2RRTdfCUoY75l/FyVEcr0ceHiz4RDBUm35lok1Q/mEDIXNtPk6E4v+be+AVAOhgTPlyU2AAAAAElFTkSuQmCC',
    'edit_off@18': 'iVBORw0KGgoAAAANSUhEUgAAABIAAAASBAMAAACk4JNkAAAAMFBMVEWjrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLaXNI5fAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAHdJREFUeNpjYGBgYGBgYHJkgIL6fwEQBsdPn4cQVv8E5i8QoX+XOT8xMjAwMPR/ydp0kYGBgYHjB4PeDwYmBgaG9g6GxA6oEMcPiMYGhv4GHELMP6FCTKw/GNo7GBgYGBh4NmhCVDEpy1yDCDHY/WmGuoxZAepGAJtFIlwx6YbMAAAAAElFTkSuQmCC',
    'edit_off@36': 'iVBORw0KGgoAAAANSUhEUgAAACQAAAAkBAMAAAATLoWrAAAAMFBMVEWjrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLaXNI5fAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAOxJREFUeNqF0rFKA0EQxvG/l4shkiJNCq3sbPMEmkewtrS28A0UBMHuqlwZUsTWSvIoloo+wHKNsObYz+LOZGdPcbr95scwLAOd6pnX5Kr/ZvsDp82xjRaS3k0yVP3g6gQVnGpqkIehrrNdNOcFZCfJw0jn8aRXFSzCeIeCPwz+SJ8RckULI/QzLkbQQc1m5O1O1RKYU6YoeLaqRdXyd9Sopv3YQQctIt+i1d+IHO73ygT1ZBE5fdYJYqSZQcBZGMOTK6Lo5itBGbMNk+eqjFCmjwsXfybsK0i6Ndciqb4zh5C5sJ4mR3dyyb/1DU2NhfWmUik8AAAAAElFTkSuQmCC',
    'locate@18': 'iVBORw0KGgoAAAANSUhEUgAAABIAAAASBAMAAACk4JNkAAAAMFBMVEU7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlJ0E5tEAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAHlJREFUeNpjYGBgYFBigIHdDAwMDEwMDAwMgjChmv/XIQydPx3nDzEwMDAwnndgYPkjwMDAwP6F8/8E/wIGBgbeA/Nn/uS5wMCgfb7hJ8N8jj+bmBgQAC4L18HC8Ou2OSPLDxEUkxkYav4fh2k6A3fLe7hBSgwMDAwAK1YmSSAQjqQAAAAASUVORK5CYII=',
    'locate@36': 'iVBORw0KGgoAAAANSUhEUgAAACQAAAAkBAMAAAATLoWrAAAAMFBMVEU7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlJ0E5tEAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAASRJREFUeNp9krFKA0EYhGfvQgKpVi1sQjwr7TwbsfMFfAKbRGy00SiCgsV1ipWvYCOWsbMMh33iG2iKWAUuKPHkLtmx8NbbuCZbDR/8PzPzLzD9FesWOvrUytHCcyw0oIUg/mxyLyL16JvEaZJk4hnokFQR2ctJibxbX9ghG7knPgHANnNvTCWEhGhRbyszRKndb6DC2wxt0Xc65Mhz+ZGhmwQVqoghml+Zez/FHk43sYEHFwCwfx69oxMDwQhr6uoSKJLsipcuUB3LKsl6FlsawQvJgTyb4+sqsKsGS7z+8dEaIuDJCmPU0mzINCGmWi0zxGK7f2wEciIdW/2WWNPlDCcqXJ6fqFAX/Tb7HP8cDUAQ29eGhaSw0PNYq4IW92LGf/wGsu+VOUHQEGAAAAAASUVORK5CYII=',
    'locate_off@18': 'iVBORw0KGgoAAAANSUhEUgAAABIAAAASBAMAAACk4JNkAAAAMFBMVEWjrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLaXNI5fAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAHZJREFUeNpjYGBgYFBigIHdDAwMDEwMDAwMgjChmv/HIQydPx3nDzEwMDAwnndgYPkjwMDAwP6F8/8E/wIGBgbeA/Nn/uS5wMCgfb7hJ8N8jj+bmBgQAC4L18HC8Ou2OSPLDxEUk5FsY2A4A3fLe7hBSgwMDAwAIlYmOXlvl4oAAAAASUVORK5CYII=',
    'locate_off@36': 'iVBORw0KGgoAAAANSUhEUgAAACQAAAAkBAMAAAATLoWrAAAAMFBMVEWjrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLaXNI5fAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAASNJREFUeNp9kj9LA0EUxGf3QgKpVi1sQjwr7TwbsRD8HjaJ2GgTowgKFtcpVn4FG7GMnWU47BO/gaZJFbig5A93yY5FbnMX7pJXDT94y7yZBZZPvppCl0OjpBG2TKE+Uyg11r2vP5wkkQ2SDOwEqpHaJ7sxKZCv+xsnZD32xE8AOGLsjaGCUBBNmteK9FBo9eoo8SVCx3Rkm5zYFv8i9BygRO3TQ2McuXdCnOHmEAd4twAA53f+L9ojwJ1gTz8+AHmSHfHdAcpTVSZZjc5WACBmOhdcqNs1/uwCp7q/xaeZj+YALq93OEQljJaSJsRSq0V62Gz1rhIHSd+crechVkw4g4UIt9cXIswIOquOjNIAuKMVbc+REin0NTUqZ8SbWPEf/wGHH5UYFpJNfAAAAABJRU5ErkJggg==',
    'save@18': 'iVBORw0KGgoAAAANSUhEUgAAABIAAAASBAMAAACk4JNkAAAAMFBMVEU7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlJ0E5tEAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAI5JREFUeNo9ziFOA0EUANA30yFB7haJ2WAwFRVNWtkjbMIFuAFwDBRobsA1kE1Fj1BXtztmFcl+DPBO8PiT3Pet+DyRx4iIiXy1eL9b3hx6rusbbieFejRvD+ssfSAe94XLBpqClpEsVsMwfFWFh1eeKDQvPJPRSMhUBCVyk1qpq7+DmLsy7/qW8fv8H/UD2hAtbHCwebcAAAAASUVORK5CYII=',
    'save@36': 'iVBORw0KGgoAAAANSUhEUgAAACQAAAAkBAMAAAATLoWrAAAAMFBMVEU7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlJ0E5tEAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAQpJREFUeNq1krFKA0EQhr+VK65Q2AUJQvCInZUEbKzuGWxthHsE3yCdjYiNj6KdWKX3BQRDrCxu1srizI7F3CV3kCKNfzX8/DP7z+wPu8ABzlutsaOOnict9XTd6l61Q5qaKv/Rby8Azn8eA3CgN2fpJIQQDsVkFMkXyR4odQns9Z+fx/F0rWq5UpeQWX1LhOZ+HseTDyiS3zcPKyj1wRoz6ahM36zx9+ISoIFVDKbqrSyLoQnDFspMtOex6xh1VwHwdUq3Y95dZ7YeP0JERIRq06jnEXDvvfFIbP9hu4l/pjLEVS8EBzjcyC+AXJNoXdd1XWsSnQ0DoKqNJ0OvupgA6TEOwrTJ0w74A28qhmUrfU/0AAAAAElFTkSuQmCC',
    'save_off@18': 'iVBORw0KGgoAAAANSUhEUgAAABIAAAASBAMAAACk4JNkAAAAMFBMVEWjrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLaXNI5fAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAI5JREFUeNo9ziFOA0EUANA30yFB7haJ2WAwFRVNWtkjbMIFuAFwDBRobsA1kE1Fj1BXtztmFcl+DPBO8PiT3Pet+DyRx4iIiXy1eL9b3hx6rusbbieFejRvD+ssfSAe94XLBpqClpEsVsMwfFWFh1eeKDQvPJPRSMhUBCVyk1qpq7+DmLsy7/qW8fv8H/UD2hAtbHCwebcAAAAASUVORK5CYII=',
    'save_off@36': 'iVBORw0KGgoAAAANSUhEUgAAACQAAAAkBAMAAAATLoWrAAAAMFBMVEWjrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLaXNI5fAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAQpJREFUeNq1krFKA0EQhr+VK65Q2AUJQvCInZUEbKzuGWxthHsE3yCdjYiNj6KdWKX3BQRDrCxu1srizI7F3CV3kCKNfzX8/DP7z+wPu8ABzlutsaOOnict9XTd6l61Q5qaKv/Rby8Azn8eA3CgN2fpJIQQDsVkFMkXyR4odQns9Z+fx/F0rWq5UpeQWX1LhOZ+HseTDyiS3zcPKyj1wRoz6ahM36zx9+ISoIFVDKbqrSyLoQnDFspMtOex6xh1VwHwdUq3Y95dZ7YeP0JERIRq06jnEXDvvfFIbP9hu4l/pjLEVS8EBzjcyC+AXJNoXdd1XWsSnQ0DoKqNJ0OvupgA6TEOwrTJ0w74A28qhmUrfU/0AAAAAElFTkSuQmCC',
    'renew@18': 'iVBORw0KGgoAAAANSUhEUgAAABIAAAASBAMAAACk4JNkAAAAMFBMVEU7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlJ0E5tEAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAJdJREFUeNpNzDEOwVAcgPGv75X3YtFKGgnpaDGZhIVeoJE4gQtIulkFuziJ1NorvMUgDiB2iUUEf0slfuuXfPCjgWjePFdjoCUih/pSoU+bxjCdAPEFaEumWKVgcgK8B2BckVC5lxdffwCo9dVrD0CUeGUbh/iFy4BtgBWXgXqCkRwYHcFeZR0O3j2wt66I7AA1o7OY8u8L5RImMrtg+zUAAAAASUVORK5CYII=',
    'renew@36': 'iVBORw0KGgoAAAANSUhEUgAAACQAAAAkBAMAAAATLoWrAAAAMFBMVEU7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlJ0E5tEAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAUhJREFUeNqVkrFKA0EQhr+7XCSxOsVCC4+kEJ/BQrDTRohPoPgCKgRbo9gJipWFRQQbS1srY2mVgBZWOWNlEzYISsTc/Ra7CaeVTrM73/7Mz8ws/Cu8CXuMwNRRu3u/CRzXHJmOJSm9xDMdS8aMlBpJ+yNU1+CwPLl8q0HJoXElFYDgVo+xRXVz6gootaq8+kPjRalDADNcOdHBm3vaS0J72ZKkDj7edr+X6cIQEIR3Ljub7sENUFTtZ7c+ORq/kcdzJp8vAVEaZlDcIsCkGUMv9PF5380OMjS/Zxuogf8T5WgRkKuC93phUZ5rYFaSPpxqPQmBKIO8uA8BwFXnyaJi6QWrehhWP1EFIEqNNiyZ0ScAUbKqryWAfCxrHKV+U4NqeXKlOVxClIYF2dXa3dlJLBhJSnacS+EVmDtvd+/XRn2F2c/05/gG98iQlCQcgfAAAAAASUVORK5CYII=',
    'renew_off@18': 'iVBORw0KGgoAAAANSUhEUgAAABIAAAASBAMAAACk4JNkAAAAMFBMVEWjrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLaXNI5fAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAJdJREFUeNpNzDEOwVAcgPGv75X3YtFKGgnpaDGZhIVeoJE4gQtIulkFuziJ1NorvMUgDiB2iUUEf0slfuuXfPCjgWjePFdjoCUih/pSoU+bxjCdAPEFaEumWKVgcgK8B2BckVC5lxdffwCo9dVrD0CUeGUbh/iFy4BtgBWXgXqCkRwYHcFeZR0O3j2wt66I7AA1o7OY8u8L5RImMrtg+zUAAAAASUVORK5CYII=',
    'renew_off@36': 'iVBORw0KGgoAAAANSUhEUgAAACQAAAAkBAMAAAATLoWrAAAAMFBMVEWjrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLaXNI5fAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAUdJREFUeNqVkrFKA0EQhr+7XCSxOsXCFB6xEJ/BQrDTRohPoPgCKgRbo9gJilhYWMQypa2VsbRKQAurnLGykQ2CEjF3v8VuwsXObZb59mdm55+Bfx0vtNcIzJx03h+2gdOaI7OxJKUXeKZryYSRUiPpcITqGhzPT6/eaVB2aFJJBSC401NsUd2cuwRKrSqv/rDwstQlgBINJzr6cE8Hif0lO5LUxcfb7fcyXRgCgvDeRZezPbgFiqqNd+uTo/kXebxk4sUyEKVhBsVtAkzay7rm4/O5P2ak+ettoCb+OMrRJiBXBe/t2qI8N8CcJH051WYSAlEGeXEfAoBG99miYvkVq3ocZj9TBSBKjbYsKekbgChZ188KQD6WLRylfkuD6vz0Wms4hCgNC7KjtbOzTiwZSUr2XJXCG7Bw1Xl/2Bj15bZo6n/L9wvQHJCDo0TFjwAAAABJRU5ErkJggg==',
    'passwd@18': 'iVBORw0KGgoAAAANSUhEUgAAABIAAAASBAMAAACk4JNkAAAAMFBMVEU7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlJ0E5tEAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAJpJREFUeNolzjtOwgAAANDXj7WRYNg8AaOxA4uRRAbdTNhdXDgAXMBwAzyIoTMXcDbR0YSwmS4Qm2hTtHVge+OLwODsE7hrmylI/ib3tQjD8PHt6xKWvWh2soFC5+eoDHGqfm4C+EBSIq3QfcViTpCPSCvpavmNxfx4vS0y0srFC7h+SvIMjLOHfQ9iN1f9HThv60Mtfr/9Pegf4dwpmwEgezoAAAAASUVORK5CYII=',
    'passwd@36': 'iVBORw0KGgoAAAANSUhEUgAAACQAAAAkBAMAAAATLoWrAAAAMFBMVEU7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlJ0E5tEAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAUdJREFUeNp9kr1KA0EUhb+su4lRkBWbFBaLnZUR0drOSsgTGF9AFAstRGzsIpg3WLG0iZUWFnkEo0gKRbQSC2FTxN+YPRaZ3R0RvM0cvnu4M2dm4E8NZXJhbfapY/dyu5J6FRstS5K+/IzkpdvtmnSdoaqefJhWP7U50XcAsKKjBI3oCgBP3QTNqAz4EPYStNcDhl/KzCgwqPkKVNViVBVngIIYCDrj9AkGKEcENPwQ4bsACB+42apZcZrd9DSrZhauWSdoDURR7wat940IVTdXFJleMf40vUkTjDAyJvdSi5apsD8139CbbQoVS3ElM3k7kqRjy9SQLu7vNq3tPOmDXJLlUHUo6KycpnP0CbmSNjLicg4HbftJC9pgWIqDlLjg+2NE7UcbdTqx5h6w0VIpT2TP8iTp2yZO7xR4/vWt8DbRySP/1g9fm46gcrDDXAAAAABJRU5ErkJggg==',
    'passwd_off@18': 'iVBORw0KGgoAAAANSUhEUgAAABIAAAASBAMAAACk4JNkAAAAMFBMVEWjrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLaXNI5fAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAJpJREFUeNolzjtOwgAAANDXj7WRYNg8AaOxA4uRRAbdTNhdXDgAXMBwAzyIoTMXcDbR0YSwmS4Qm2hTtHVge+OLwODsE7hrmylI/ib3tQjD8PHt6xKWvWh2soFC5+eoDHGqfm4C+EBSIq3QfcViTpCPSCvpavmNxfx4vS0y0srFC7h+SvIMjLOHfQ9iN1f9HThv60Mtfr/9Pegf4dwpmwEgezoAAAAASUVORK5CYII=',
    'passwd_off@36': 'iVBORw0KGgoAAAANSUhEUgAAACQAAAAkBAMAAAATLoWrAAAAMFBMVEWjrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLaXNI5fAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAUhJREFUeNp9kr1KA0EUhb+NuyYqyIiNhcViZ5WIaG1nJeQJ1BcQxUILERu7FOYBhBVLm1hpYeEj+INYKGKqYCFsCv9ikj0Wmc2OCN5mDt893JkzM/CnBjI5vzbTaLo9b1dSu+yiJUnSt8nIoPSwXZFuM7SihoFpdfu2XNwJAZZ1lKJh3QAQ6C1FRZUAA1E7RXttoPBaoqjQost3YEXXjKic66EwAcLmGF3CHvKIgZqJEMYHQBjgbqvixLl8659m1c7Ct+s41z0xpE+L1rtWRKraK4ptbyhp2d6kDUYUW5N/pQXHlN+fmqvpwzVFSqSknJmCHUnSsWOqSRdPj5vOdoH0hZdmOVAV8jor9dPl1AJvQhsZ8TmHw3v3SfPaoCAlYZ/4YMwo8X3dRc1motlnXLQ4MUjszgokqeOSXPsUePn1rQg20Umdf+sHaeuOsE73+KgAAAAASUVORK5CYII=',
    'reboot@18': 'iVBORw0KGgoAAAANSUhEUgAAABIAAAASBAMAAACk4JNkAAAAMFBMVEW0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0IxjrKE+QAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAIFJREFUeNo9zqENwlAAANH3fygC9btBg8ORoNgCg+8GnYAB0DgUGxSPYQACOAwbYKpAlFBEA6dOXQ6M/ThAhPxvehvuMCpF84SwwTk5cRfjpIHNNA5aco7TGDq2NEXsAms08dMXiysPUCeqEtkLoyeqPUJ9W67aBNmley/6izBL+ALU0BzAcO3F9wAAAABJRU5ErkJggg==',
    'reboot@36': 'iVBORw0KGgoAAAANSUhEUgAAACQAAAAkBAMAAAATLoWrAAAAMFBMVEW0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0IxjrKE+QAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAASZJREFUeNqNkrtKA1EQhr8zWZQI4op92FIQJE8QBUmfJxB7NfoGAZ8gtY1aaWdnKcHCStxUWnoDG4VdNBhI3B2LsycnsASc6j8fc/mZOTA75nZK6ODHKXEikhJKtYR8/B9VjtxrdbMQjV+gMwTigc0yZ5OadCECgWr07lDbdEGgybZDDzRB4HDUcyjrzgOIvuLaU9M6QsC19/RBCyGg59GYOoLQBwgNQE6EYEgB+hmApssEhHYr58aaFWBRI9/LJC8IyjQKE4RsGlV4RshoeWQdSfLlUU3rAJcjj07GAGzopDLQAQBV/Xaooad2cJwXK68kWWjVug6t6OibM/OktxHInrp0WFP9vDi+V73xs/dVVVUfpw5sdhPV/Mr2NgVc2Vq6i2d+yD8Fnl/lEpTzCgAAAABJRU5ErkJggg==',
    'reboot_off@18': 'iVBORw0KGgoAAAANSUhEUgAAABIAAAASBAMAAACk4JNkAAAAMFBMVEWjrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLaXNI5fAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAIFJREFUeNo9zqENwlAAANH3fygC1W7Q4HBNUGyBwXeDTsAA6DoUGxSPYQACOAwbYKpAlFBEA6dOXQ5M/ThAhOxvBhvvMClFixShxjl14i7GWQt1EUcdGccihp4tbR77wAZt/AzF/MoDNClVieSFyRPVHqG5rdZdCsmlfy+HizDP8QXVEBzQwfao6wAAAABJRU5ErkJggg==',
    'reboot_off@36': 'iVBORw0KGgoAAAANSUhEUgAAACQAAAAkBAMAAAATLoWrAAAAMFBMVEWjrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLaXNI5fAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAASZJREFUeNqNkrtKA1EQhr8zWZQI4op92FIQJE8QBUmfJxB7NfoGAZ8gtY1aaWdnKcHCStxUWnoDG4VdNBhI3B2LsycnsASc6j8fc/mZOTA75nZK6ODHKXEikhJKtYR8/B9VjtxrdbMQjV+gMwTigc0yZ5OadCECgWr07lDbdEGgybZDDzRB4HDUcyjrzgOIvuLaU9M6QsC19/RBCyGg59GYOoLQBwgNQE6EYEgB+hmApssEhHYr58aaFWBRI9/LJC8IyjQKE4RsGlV4RshoeWQdSfLlUU3rAJcjj07GAGzopDLQAQBV/Xaooad2cJwXK68kWWjVug6t6OibM/OktxHInrp0WFP9vDi+V73xs/dVVVUfpw5sdhPV/Mr2NgVc2Vq6i2d+yD8Fnl/lEpTzCgAAAABJRU5ErkJggg==',
    'factory_reset@18': 'iVBORw0KGgoAAAANSUhEUgAAABIAAAASBAMAAACk4JNkAAAAMFBMVEW0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0IxjrKE+QAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAIRJREFUeNpNzL0NQWEYgNHjI3FLt1RIbqPSKFXMwARG0BnAAKJRM4HojaAxAYnolQhehZ94+ufwKw01x33Izq2IWCI7xSTvPNtUY43ujiy2I9JVum/gOa+VPkQvT5UFOBSp0v/CjwSKg9IFTGusCqQbGsePrLyf5J1HG+oRMXv/zfHAfy/r+CSKSZDW1QAAAABJRU5ErkJggg==',
    'factory_reset@36': 'iVBORw0KGgoAAAANSUhEUgAAACQAAAAkBAMAAAATLoWrAAAAMFBMVEW0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0Ixi0IxjrKE+QAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAASBJREFUeNqV0b1OAkEYheF3Rzf+BJKBxKgNkcaa1g47GhO8ArwFDTdgYmEH9iTa2Vpa2lphYs+GjoasiVExOHssmM0sVDrlkzM/3xn447IARDbI5gQ4HIymj8c51TLLUSpJ7jLQnqQslbKznMxQ82692hrq25M70bwJECe686lUfsO+j9Wk1/yqntqe7q99bEtjT9KnjyVfAOUidZwlYq0L0WRxFaX304eVeTfUxyyTo4HBXBQowwJlV6AoHWOoFJsCMKRFimyKQRwUE28YHI1A67wAsZ4CldQEovQjUMdZgF54RZTMfGH9nHw5xJqtVAi3ulkuGrbl2kvfAfT0c1WvtobhBOJk8bWuHZ6zm0iSOy9OvzMYTZ9PVyquVPjX+gVqO4UOvGPf2AAAAABJRU5ErkJggg==',
    'factory_reset_off@18': 'iVBORw0KGgoAAAANSUhEUgAAABIAAAASBAMAAACk4JNkAAAAMFBMVEWjrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLaXNI5fAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAIRJREFUeNpNzL0NQWEYgNHjI3FLt1RIbqPSKFXMwARG0BnAAKJRM4HojaAxAYnolQhehZ94+ufwKw01x33Izq2IWCI7xSTvPNtUY43ujiy2I9JVum/gOa+VPkQvT5UFOBSp0v/CjwSKg9IFTGusCqQbGsePrLyf5J1HG+oRMXv/zfHAfy/r+CSKSZDW1QAAAABJRU5ErkJggg==',
    'factory_reset_off@36': 'iVBORw0KGgoAAAANSUhEUgAAACQAAAAkBAMAAAATLoWrAAAAMFBMVEWjrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLajrLaXNI5fAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAASFJREFUeNqV0a1OA0EUhuF3Bjb8pE22JAQwDRh0Nao4DEl7BeUSgPQGcDiKb1IcFonEokqC76auphkSApTA7IfYWXZbBSOfOZlz5jvwxxMDYOJCVifAfn80vT/MqZ7GHDhJ8hcFbUtKnZSe5GSH+u7ubRwN9RnIH+urCRAluglVTqfZ7U6aldWl57zVlVqBbi/Du2saB5LeMzLJB0C1RHR8jGGpC2aStaLy2r5b+O+Keth58jSw2PMSpcRA1ZfIuDGWWjkpAIsrk4kdFrFbrnjB4mkUtMwTEOmhoIqagHFvBXV8DHBVTGGSWQisl1MIh0izhQhh4K5D0Pk+WJdvza0DGORL+32BKMlW61vFOFuJJPmz8u83+6PpY3sh4lqNf50fhT+FEAZgk1kAAAAASUVORK5CYII=',
    'iface@18': 'iVBORw0KGgoAAAANSUhEUgAAABIAAAASBAMAAACk4JNkAAAAMFBMVEU7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlJ0E5tEAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAJFJREFUeNpFzbEOwVAYxfH/bSsdNFFhVU8g6WCXcHd2L3HfrBZTW7prEKulu6QkTEU+ww3Ocn45y3EBbxpVAJBIY6EanYTgAO/siNUvqk7r0HIp6++a2mplpZS7BYpoX127Tm8IHQP4N/APlxh38zJMztsV7Wd+Zx4HBUHhPv4fHnoA9DWMRAy+yEmpmeQwDssP9H8shM9VdcUAAAAASUVORK5CYII=',
    'iface@36': 'iVBORw0KGgoAAAANSUhEUgAAACQAAAAkBAMAAAATLoWrAAAAMFBMVEU7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlJ0E5tEAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAARdJREFUeNqNkbFKA0EQhr/d2+OCYLLY2FjkESxPsLcTOxvByv5QIocgaexs8gi22mjpE9hqqdikSJ1cI0ck5LfYXLyQGJxmlt2Zf775F5bChGTPPHro118OJWmwUPwqSZN6oxmOe60k2ypqiqM+NKc+6P6O8bOjXYZYcyWotN0sN/JWwp9cK+ijkD5Lt3N1USy2dss1EA6wHfCpywX3/Zp2iEGdIMQkrGuG454A8JU/ZjQ3uDn1YEH1cQVExJf7xrwAcHySNjbf4VGS7gA2JOkbYknSF8CpJOnIWZ7fSPcAdifXRDdtEmXVgt0SEmUW/PwnDBiwUMw9Fgj+V+XgYJvUAHiXE7GKawW9IT6H6W3YsQ0fT8vW8wNn0JLbTZ2CvAAAAABJRU5ErkJggg==',
    'app@64': 'iVBORw0KGgoAAAANSUhEUgAAAEAAAABABAMAAABYR2ztAAAAMFBMVEUVZcAVZcAVZcAVZcAVZcAVZcAVZcAVZcAVZcAVZcAVZcAVZcAVZcAVZcAVZcAVZcC+ZqBeAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAcJJREFUeNrllTFLHEEUx3/vPOMZEpjFLkW45oqEgJf2GlNYBjyEs9NLJ3bX2vgZAqlSGUmXQPIFQgjpRa0sRMXiKsntET3I6t29FLszu3u7a4oIFr5imXnzZ+bNb2b/A/8bkuxMLQNwtF8gLu+pqqoOXxUI2hrFoGA13wrGJs6W4uYDl5Y3iXUTJYL2ATFUcwXAxw4w28UUCdQHglSq9C9Q90SQBlVrATNF2ll18TZ3iWHc/JE/xZ6dYFiwxgsr+EnqErg4f/QsqKD9g9d/iur0nqjueDds0w+iO3EXqGWzbjfV+ATAaKOf1K1pJrqpeTQnkn/o4zxB8kAW8gQXiSKr8Hmi+la0QUtSVyYEvpngYAqc53ZIijHgSfIUPQHxNIQ5/b3nq/Z+vYvHGyc91XHvvAnAtnWmph2ftm4VAMxMkElx6wBPXe/KCr661BklXmYvcN2l5ihj0PU+0OiICasWQ7AKyHsjQFvHACw5/xRfL0PcOghBmQxrcZ/7ZGKeZDh4lkMZkN3M+1c5jtnNu6O7zpiV/gYeauaxc8+ffgBKpxl3dI45qgI8j3qHcQlbUWonLK1WB9BvsSHIogEYfeEW4i8vw1k77tnSQwAAAABJRU5ErkJggg==',
    'about@18': 'iVBORw0KGgoAAAANSUhEUgAAABIAAAASBAMAAACk4JNkAAAAMFBMVEU7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlJ0E5tEAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAAHlJREFUeNpNzrERwQAYQOHPn5O0VNpswARSSOvOCKk0GqqsYCKdDZRa4c4AnNRCoZDmle+9BLHonmBy/bx3kFwP5eY9w/yM5Z3BZeTHrGVPsY30CE6VaQXSV+Q3aroIWEPcch5EF82KMcNG1lJTbHvmf6138L8iyhxfrUIoRvW6jHMAAAAASUVORK5CYII=',
    'about@36': 'iVBORw0KGgoAAAANSUhEUgAAACQAAAAkBAMAAAATLoWrAAAAMFBMVEU7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlI7RlJ0E5tEAAAAEHRSTlMAESIzRFVmd4iZqrvM3e7/dpUBFQAAASBJREFUeNp1kjFLw1AUhb/cYoUWMTh2eqI/oLg6dOikiy7OZnXqP7CzIHQRcazgbsBZXR0cujs0m2ugaqWlOQ5pmkjSu7zH9+55cM89UKra8tw/626O/z1cS9Jbu0AeJUmauRW6lD7urp6lz4y0pAcfvGPpPiXeq57S26HmPgAN/Wb9Aw0B6CvIUF1TAMubYCAHNBQC1AOApoZARw6g95NKJhinsygXJuEGMP4iF3KyANOoOO2WnBlRESXkaClMcOYRA3BxC4DwLVO41c1EOmms1ANiE674vRHbgt0i2mFkSbRXRAdJZIR1AHwPgGAOdNQGOP8GqGlSbU6tbGGF0TTL66hYGi3pxgfvaLVa6GcBmJZj0l4TJi+LXHf7/YV19QcEUYvmEjTiHwAAAABJRU5ErkJggg==',
}


if __name__ == "__main__":
    main()

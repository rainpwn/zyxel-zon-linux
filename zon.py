#!/usr/bin/env python3
"""zon.py - discover and manage Zyxel devices from Linux over ZDP, the layer 2
protocol used by Zyxel One Network Utility (ZON). Interactive shell or one-shot commands.

  sudo ./zon.py eth0                       # discover, then a "zon>" shell
  sudo ./zon.py eth0 --json discover       # one command, JSON output

The protocol is documented in PROTOCOL.md.
"""
import argparse, base64, cmd, getpass, hashlib, hmac, ipaddress, json, os, random, select, shlex, socket
import struct, subprocess, sys, time

ETH_P_ZDP = 0xC1C1
DISCOVERY_MAC = bytes.fromhex("01a0c5111111")
DISCOVERY, QUERY, GET, SET = 0x8000, 0x0002, 0x4004, 0x4006
# (key, iv) rows of the table at 0x4b87e0 in ZON.exe (sha256 1e5f58c9...944e). The key comes from row
# "mode", the IV from row "Password Security >> 8". Rows 0 and 4 are not AES rows, but ZON still reads
# 16 IV bytes from them, so they are kept as found.
AES = [(bytes.fromhex(k), bytes.fromhex(iv)) for k, iv in [
    ("0000000046393238373339384637354533374231433432354238433339464342", "00000046393238373339384637354533"),
    ("4639323837333938463735453337423143343235423843333946434231343246", "41324536334437393131463144433239"),
    ("4639323837333938463735453337423143343235423843333946434231343246", "41324536334437393131463144433239"),
    ("4639323837333938463735453337423143343235423843333946434231343246", "41324536334437393131463144433239"),
    ("0000463932383733393846373545333742314334323542384333394643423134", "00463932383733393846373545333742"),
]]

# type: (name, format, permissions) from the TLV table in ZON.exe; d=discovery q=query g=get s=set
TLV = {
    1: ("Error", "hex", ""), 2: ("Capabilities", "hex", "dqg"), 3: ("System MAC", "mac", "dqg"),
    4: ("Device Model", "str", "dqg"), 5: ("FW Version", "str", "dqg"), 6: ("DHCP Setting", "int", "dqgs"),
    7: ("IPv4 Address", "ip", "dqgs"), 8: ("IPv4 Netmask", "ip", "dqgs"), 9: ("IPv4 Gateway", "ip", "dqgs"),
    10: ("IPv4 DNS Server 1", "ip", "dqgs"), 11: ("IPv4 DNS Server 2", "ip", "dqgs"),
    12: ("IPv4 NTP Server 1", "ip", "dqgs"), 13: ("IPv4 DNS Server 1 (Domain Name)", "str", "dqgs"),
    14: ("SNMP GET Community", "str", "gs"), 15: ("SNMP SET Community", "str", "gs"),
    16: ("SNMP TRAP Community", "str", "dqgs"), 17: ("SNMP TRAP Server1", "ip", "dqgs"),
    18: ("IPv4 Syslog server 1", "ip", "dqgs"), 19: ("IPv4 Syslog server 1 (Domain Name)", "str", "dqgs"),
    20: ("IPv4 Syslog server 2", "ip", "dqgs"), 21: ("IPv4 Syslog server 2 (Domain Name)", "str", "dqgs"),
    22: ("System Name", "str", "dqgs"), 23: ("Password", "hex", "s"), 24: ("Location", "str", "dqgs"),
    25: ("DHCP renew action", "act", "s"), 26: ("Device reboot action", "act", "s"),
    27: ("Locator LED flash", "int", "s"), 28: ("Device IPv4 Web GUI URL", "str", "dqg"),
    29: ("F/W Upgraded Protocol information 1", "str", "dqg"), 30: ("F/W Upgraded Protocol information 2", "str", "dqg"),
    31: ("TFTP F/W Upgraded", "str", "s"), 32: ("Reboot After F/W upgrade OK", "act", "s"),
    33: ("Device Type", "int", "dqg"), 34: ("Explicit Save", "act", "s"), 35: ("AP Operational Mode", "int", "dqg"),
    36: ("Reset Configuration to Default", "act", "s"), 37: ("Device Uptime", "uptime", "dqg"),
    38: ("DHCP Status", "int", "dqg"), 39: ("Locator LED Control", "int", "dqgs"),
    40: ("Locator LED Setting", "int", "dqgs"), 41: ("Serial Number", "str", "g"), 42: ("HW Version", "str", "dqg"),
    43: ("Password Security", "int", "dqg"), 44: ("Salt", "hex", "dq"), 45: ("Nebula", "int", "dqgs"),
    46: ("AC Discovery", "str", "dqgs"), 47: ("Link-local IPv6 Address", "ip6", "dqg"),
    48: ("Latest Global Unicast IPv6 Address", "ip6", "dqgs"),
}
SIZE = {6: 2, 27: 2, 39: 2, 40: 4, 45: 2}  # fixed-size integers used in set requests


def fmt(kind, v):
    if kind == "str": return v.split(b"\0")[0].decode("utf-8", "replace")
    if kind == "ip" and len(v) == 4: return str(ipaddress.IPv4Address(v))
    if kind == "ip6" and len(v) == 16: return str(ipaddress.IPv6Address(v))
    if kind == "mac" and len(v) == 6: return v.hex(":")
    if kind == "int" and v: return int.from_bytes(v, "big")
    if kind == "uptime" and v:  # seconds (ZON multiplies by 1000 and keeps milliseconds)
        d, s = divmod(int.from_bytes(v, "big"), 86400)
        return f"{d}d {s // 3600:02}:{s % 3600 // 60:02}:{s % 60:02}"
    return v.hex()


def encode(t, text):
    kind = TLV[t][1]
    if kind == "str": return text.encode() + b"\0"
    if kind == "ip": return ipaddress.IPv4Address(text).packed
    if kind == "ip6": return ipaddress.IPv6Address(text).packed
    if kind == "int": return int(text, 0).to_bytes(SIZE.get(t, 2), "big")
    if kind == "act": return b""
    return bytes.fromhex(text)


def tlv_id(name):
    if name.isdigit(): return int(name)
    hits = [t for t, (n, *_) in TLV.items() if name.lower() in n.lower().replace(" ", "")]
    if len(hits) != 1:
        raise ValueError(f"unknown or ambiguous field: {name} -> {[TLV[t][0] for t in hits]}")
    return hits[0]


# ---- authentication key (GenDevCipher in ZON.exe) ----

def aes_cbc_decrypt(key, iv, data):
    r = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-K", key.hex(), "-iv", iv.hex()],
                       input=data, capture_output=True)
    if r.returncode:
        raise ValueError("could not decrypt the salt: " + r.stderr.decode().strip())
    return r.stdout


def rc4(key, data):
    S, j = list(range(256)), 0
    for i in range(256):
        j = (j + S[i] + key[i % len(key)]) & 0xFF
        S[i], S[j] = S[j], S[i]
    out, i, j = bytearray(), 0, 0
    for b in data:
        i = (i + 1) & 0xFF; j = (j + S[i]) & 0xFF
        S[i], S[j] = S[j], S[i]
        out.append(b ^ S[(S[i] + S[j]) & 0xFF])
    return bytes(out)


def md5_cipher(pw, s):
    d1 = hashlib.md5(pw + b"zyxel" + bytes([s])).digest()
    d2 = hashlib.md5(pw + b"zyxel" + bytes([s]) + d1).digest()
    buf = d1 + d2[:5] + bytes([s, 1])
    return base64.b64encode(buf + bytes([(0xFF - sum(buf)) & 0xFF]))


def decrypt_salt(dev):
    sec = dev.get("Password Security") or 0
    salt = bytes.fromhex(dev.get("Salt") or "")
    hexsalt = salt[:len(salt) - 1].decode()  # ZON drops the last byte (NUL) and reads the hex pairs backwards
    enc = bytes.fromhex(hexsalt[:len(hexsalt) // 2 * 2])[::-1]
    return int(aes_cbc_decrypt(AES[sec & 0xFF][0], AES[sec >> 8][1], enc).split(b"\0")[0] or b"0") & 0xFF


def new_password_tlv(dev, new):
    """Cleartext value of TLV 23 for a password change (FUN_00409110 in ZON.exe)."""
    mode, pw = (dev.get("Password Security") or 0) & 0xFF, new.encode("latin1")
    if mode == 2:  # new 1-byte salt, different from the old one; the device reads it from the blob
        old = dev["_salt"]
        return md5_cipher(pw, random.choice([s for s in range(1, 255) if s != old]))
    if mode == 4:  # new 8-character salt + password + NUL
        abc = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnopqrstuvwxyz"
        return "".join(random.choice(abc) for _ in range(8)).encode() + pw + b"\0"
    return pw


def auth_key(dev, password):
    sec = dev.get("Password Security") or 0
    mode = sec & 0xFF
    salt = bytes.fromhex(dev.get("Salt") or "")
    pw = password.encode("latin1")
    if mode == 0:
        return pw
    if mode == 4:
        return hashlib.sha256(salt + pw).hexdigest().encode()
    if mode not in (1, 2, 3):
        raise ValueError(f"unknown Password Security mode {mode}")
    dev["_salt"] = decrypt_salt(dev)
    return md5_cipher(pw, dev["_salt"])


# ---- transport ----

class ZDP:
    def __init__(self, iface, debug=False):
        self.debug = debug
        try:
            hwtype = open(f"/sys/class/net/{iface}/type").read().strip()
        except FileNotFoundError:
            sys.exit(f"no interface '{iface}'; available: {', '.join(sorted(os.listdir('/sys/class/net')))}")
        if hwtype != "1":  # ARPHRD_ETHER: ZDP is layer 2 and does not cross tun/VPN links
            sys.exit(f"'{iface}' is not an Ethernet interface. ZDP only works on the devices' own LAN/VLAN, "
                     "not through a VPN or IP tunnel")
        self.s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ZDP))
        self.s.bind((iface, ETH_P_ZDP))
        self.mac = self.s.getsockname()[4][:6]

    def send(self, dst, code, tlvs, key=None):
        tid = random.getrandbits(32)
        body = b"".join(bytes([t, len(v)]) + v for t, v in tlvs) + b"\0\0"
        pdu = struct.pack(">HH6sI20s", 1, code, dst, tid, b"\0" * 20) + body
        if key is not None:  # sign the real bytes: the device strips padding before checking
            pdu = pdu[:14] + hmac.new(key, pdu, hashlib.sha1).digest() + pdu[34:]
        # Pad short frames with zeros ourselves. Padding added further along the path is not
        # guaranteed to be zeros, and devices rejected signed requests that arrived padded that way.
        pdu = pdu.ljust(46, b"\0")
        self.s.send(dst + self.mac + struct.pack(">H", ETH_P_ZDP) + pdu)
        if self.debug:
            print(f"\n> {dst.hex(':')} {pdu.hex()}", file=sys.stderr)
        return tid

    def collect(self, pending, timeout=3.0, many=False):
        """pending: {tid: dst}. Without many, stops once every request has an answer."""
        out, end = {}, time.monotonic() + timeout
        while pending and (left := end - time.monotonic()) > 0 and select.select([self.s], [], [], left)[0]:
            f = self.s.recv(2048)
            r = parse(f)
            if not r or f[6:12] == self.mac or r[0] & 0x8000 or r[1] not in pending:
                continue
            dst = pending[r[1]]
            if dst != DISCOVERY_MAC and f[6:12] != dst:
                continue
            if self.debug:
                print(f"\n< {f[6:12].hex(':')} {f[14:].hex()}", file=sys.stderr)
            out[f[6:12].hex(":")] = r[2]
            if not many:
                del pending[r[1]]
        return out

    def request(self, dst, code, tlvs, key=None, timeout=3.0, many=False):
        return self.collect({self.send(dst, code, tlvs, key): dst}, timeout, many)


def status(msg):
    """Status line on stderr, rewritten in place; an empty message clears it."""
    if sys.stderr.isatty():
        print(f"\r\033[K{msg}", end="", file=sys.stderr, flush=True)
    elif msg:
        print(msg, file=sys.stderr)


def parse(frame):
    p = frame[14:]
    if len(p) < 35 or frame[12:14] != b"\xc1\xc1":
        return None
    ver, code, _, tid = struct.unpack_from(">HH6sI", p)
    if ver != 1:
        return None
    fields, i = {}, 34
    while i + 2 <= len(p) and p[i]:
        t, n = p[i], p[i + 1]
        name, kind, _ = TLV.get(t, (f"TLV {t}", "hex", ""))
        fields[name] = fmt(kind, p[i + 2:i + 2 + n])
        i += 2 + n
    return code, tid, fields


# ---- shell ----

class Shell(cmd.Cmd):
    prompt = "zon> "

    def __init__(self, zdp, as_json):
        super().__init__()
        self.zdp, self.json = zdp, as_json
        self.devs, self.pw = {}, {}

    def emptyline(self): pass
    def do_exit(self, _): "exit"; return True
    do_quit = do_EOF = do_exit

    def onecmd(self, line):
        try:
            return super().onecmd(line)
        except (ValueError, KeyError, IndexError, OSError) as e:
            print("error:", e)

    def out(self, obj):
        if self.json:
            print(json.dumps({m: {k: v for k, v in f.items() if not k.startswith("_")} for m, f in obj.items()},
                             indent=2)); return
        for mac, f in obj.items():
            print(mac)
            for k, v in f.items():
                if not k.startswith("_"):
                    print(f"  {k:38} {v}")

    def dev(self, ref):
        if not ref:
            raise ValueError("give the device number (#) or MAC, e.g. show 1")
        macs = list(self.devs)
        mac = macs[int(ref) - 1] if ref.isdigit() else ref.lower()
        if mac not in self.devs:
            raise KeyError(f"device {ref} not found, run discover")
        return mac, self.devs[mac]

    def key(self, mac, dev):
        if mac not in self.pw:
            ask = getpass.getpass if sys.stdin.isatty() else input  # read stdin when piped or scripted
            pw = ask(f"password for {mac} ({dev.get('Device Model', '')}, input hidden): ")
            if not pw:
                raise ValueError("empty password, nothing sent")
            self.pw[mac] = pw
        k = auth_key(dev, self.pw[mac])
        status(f"waiting for {mac}...")
        return k

    def check(self, mac, reply):
        status("")
        r = reply.get(mac)
        if r is None:
            print("no answer (wrong password or unsupported command?)")
        elif "Error" in r:
            e = bytes.fromhex(r["Error"])
            field = TLV.get(e[1], (f"TLV {e[1]}",))[0] if len(e) > 1 else "?"
            msg = e[4:].split(b"\0")[0].decode("utf-8", "replace") if len(e) > 4 else ""
            print(f"device returned error {e[0] if e else '?'} on field {field}", msg)
            self.pw.pop(mac, None)
        else:
            return r

    def scan(self, timeout=3.0):
        status(f"searching the LAN ({timeout:g}s)...")
        self.devs = self.zdp.request(DISCOVERY_MAC, DISCOVERY, [(2, b""), (3, b""), (4, b"")],
                                     timeout=timeout, many=True)
        status(f"found {len(self.devs)} devices, reading details...")
        # Discovery only returns the fields it asks for. Like ZON, follow up with an unauthenticated query
        # for every supported field. A device that refuses one field (Error = code, TLV type) answers with
        # the error alone, so ask again without that field.
        todo = {mac: [t for t in bytes.fromhex(d.get("Capabilities", "")) if "q" in TLV.get(t, ("", "", ""))[2]]
                for mac, d in self.devs.items()}
        for _ in range(10):
            pending = {self.zdp.send(bytes.fromhex(m.replace(":", "")), QUERY, [(t, b"") for t in ids]):
                       bytes.fromhex(m.replace(":", "")) for m, ids in todo.items() if ids}
            if not pending:
                break
            asked, todo = todo, {}
            for mac, f in self.zdp.collect(pending, timeout).items():
                err = bytes.fromhex(f.get("Error", ""))
                if len(err) >= 2 and err[1] in asked[mac]:
                    todo[mac] = [t for t in asked[mac] if t != err[1]]
                else:
                    self.devs[mac].update(f)
        # stable numbering: by IP, then MAC (replies arrive in random order)
        def order(kv):
            ip = str(kv[1].get("IPv4 Address", "")).split(".")
            return (tuple(map(int, ip)) if len(ip) == 4 else (256,)), kv[0]
        self.devs = dict(sorted(self.devs.items(), key=order))
        status("")

    def do_discover(self, arg):
        "discover [seconds]: find devices on the LAN"
        self.scan(float(arg or 3))
        if self.json:
            return self.out(self.devs)
        cols = ["Device Model", "IPv4 Address", "FW Version", "System Name"]
        print(f"{'#':>2}  {'MAC':17}  " + "  ".join(f"{c:18}" for c in cols))
        for i, (mac, f) in enumerate(self.devs.items(), 1):
            print(f"{i:>2}  {mac:17}  " + "  ".join(f"{str(f.get(c, '')):18}" for c in cols))

    def do_show(self, arg):
        "show N|MAC: every field read during discovery"
        mac, d = self.dev(arg)
        self.out({mac: d})

    def do_fields(self, _):
        "fields: list ZDP fields and permissions (d=discovery q=query g=get s=set)"
        for t, (n, k, p) in TLV.items():
            print(f"{t:>3}  {n:38} {k:6} {p}")

    def do_query(self, arg):
        "query N|MAC field ...: unauthenticated read of any field, even if the device does not list it"
        a = shlex.split(arg)
        mac, d = self.dev(a[0])
        r = self.check(mac, self.zdp.request(bytes.fromhex(mac.replace(":", "")), QUERY,
                                             [(tlv_id(x), b"") for x in a[1:]]))
        if r:
            d.update(r)
            self.out({mac: r})

    def do_get(self, arg):
        "get N|MAC [field ...]: authenticated read (default: every readable field)"
        a = shlex.split(arg)
        mac, d = self.dev(a[0])
        caps = bytes.fromhex(d.get("Capabilities", ""))
        ids = [tlv_id(x) for x in a[1:]] or [t for t in caps if "g" in TLV.get(t, ("", "", ""))[2]]
        key = self.key(mac, d)
        for _ in range(len(ids)):  # a device rejects the whole get over one unreadable field: retry without it
            reply = self.zdp.request(bytes.fromhex(mac.replace(":", "")), GET, [(t, b"") for t in ids], key)
            err = bytes.fromhex(reply.get(mac, {}).get("Error", ""))
            if len(err) < 2 or err[1] not in ids or len(ids) == 1:
                break
            ids.remove(err[1])
        r = self.check(mac, reply)
        if r:
            self.out({mac: r})

    def set(self, ref, pairs, confirm=None, restarts=False):
        mac, d = self.dev(ref)
        if confirm and input(f"{confirm} on {mac} ({d.get('Device Model', '')})? [yes/no] ").strip().lower() != "yes":
            return print("cancelled")
        reply = self.zdp.request(bytes.fromhex(mac.replace(":", "")), SET, pairs, self.key(mac, d))
        if restarts and not reply:  # the device restarts before answering
            status("")
            return print(f"sent; the device restarts without answering. Check later with: query {mac} uptime")
        if self.check(mac, reply) is not None:
            print("ok")

    def do_set(self, arg):
        "set N|MAC field value [field value ...]: e.g. set 1 ipv4address 192.168.1.20 netmask 255.255.255.0"
        a = shlex.split(arg)
        pairs = [(tlv_id(k), encode(tlv_id(k), v)) for k, v in zip(a[1::2], a[2::2])]
        for t, _ in pairs:
            if "s" not in TLV[t][2]:
                raise ValueError(f"{TLV[t][0]} is read-only")
        self.set(a[0], pairs, confirm="Set " + ", ".join(f"{TLV[t][0]}={v}" for (t, _), v in zip(pairs, a[2::2])))

    def do_save(self, arg):
        "save N|MAC: save the running configuration (Explicit Save)"
        self.set(arg, [(34, b"")])

    def do_locate(self, arg):
        "locate N|MAC [on|off]: blink the locator LED"
        a = shlex.split(arg)
        self.set(a[0], [(27, (0 if a[1:] == ["off"] else 1).to_bytes(2, "big"))])

    def do_reboot(self, arg):
        "reboot N|MAC: reboot the device"
        self.set(arg, [(26, b"")], confirm="Reboot", restarts=True)

    def do_renew(self, arg):
        "renew N|MAC: renew the DHCP lease"
        self.set(arg, [(25, b"")])

    def do_factory_reset(self, arg):
        "factory_reset N|MAC: reset the configuration to factory defaults"
        self.set(arg, [(36, b"")], confirm="FACTORY RESET", restarts=True)

    def do_passwd(self, arg):
        "passwd N|MAC: change the device password"
        mac, d = self.dev(arg)
        dst = bytes.fromhex(mac.replace(":", ""))
        fresh = self.zdp.request(dst, QUERY, [(43, b""), (44, b"")]).get(mac)  # fresh salt, as ZON does
        if not fresh:
            return print("the device did not answer the salt query")
        d.update(fresh)
        key = self.key(mac, d)
        ask = getpass.getpass if sys.stdin.isatty() else input
        new = ask("new password: ")
        if new != ask("again: ") or not 4 <= len(new) <= 200:
            return print("passwords differ or length is outside 4-200")
        val = new_password_tlv(d, new)
        if self.check(mac, self.zdp.request(dst, SET, [(23, rc4(key, val))], key)) is not None:
            self.pw[mac] = new
            d.update(self.zdp.request(dst, QUERY, [(43, b""), (44, b"")]).get(mac) or {})
            print("password changed")

    def do_logout(self, arg):
        "logout [N|MAC]: forget stored passwords"
        self.pw.pop(self.dev(arg)[0], None) if arg else self.pw.clear()


def main():
    p = argparse.ArgumentParser(description="Zyxel ZON for Linux (ZDP protocol)")
    p.add_argument("iface")
    p.add_argument("command", nargs="*", help="run one command (e.g. discover); without it, open the shell")
    p.add_argument("--json", action="store_true")
    p.add_argument("--debug", action="store_true", help="print every ZDP payload sent and received, in hex")
    a = p.parse_intermixed_args()
    if os.geteuid() != 0:
        sys.exit("run as root (raw sockets)")
    sh = Shell(ZDP(a.iface, a.debug), a.json)
    if a.command:
        if a.command[0] != "discover":
            sh.scan()
        sh.onecmd(shlex.join(a.command))
    else:
        sh.onecmd("discover")
        sh.cmdloop("ZON for Linux - type 'help' for commands")


if __name__ == "__main__":
    main()

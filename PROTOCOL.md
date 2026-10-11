# ZDP: the protocol behind Zyxel One Network Utility

This document describes the protocol ZON uses to find and configure Zyxel devices. I recovered it by decompiling `ZON.exe` (SHA-256 `1e5f58c9f9664eed6bac24a1d4a7439bd5ddaa886a2489d9e7ffdb5cdc23944e`) with Ghidra. Function and data addresses below refer to that build.

Each section says where its content comes from:

- **observed**: seen on real devices (see the hardware list in the README)
- **from code**: read from the decompiled binary, not yet confirmed on a device

ZON logs this protocol as "ZDP". All multi-byte integers are big-endian.

## Ethernet framing (observed)

ZDP runs directly on Ethernet with EtherType `0xC1C1`. There is no IP or UDP layer, so it stays inside one broadcast domain.

| Request | Destination MAC |
|---|---|
| Discovery | `01:a0:c5:11:11:11` (Zyxel OUI `00:a0:c5` with the multicast bit set) |
| Everything else | The device's MAC |

ZON sends and receives with WinPcap using the capture filter `ether proto 0xc1c1`. Replies are unicast to the sender's MAC.

## Header (observed)

Every frame carries a 34-byte header after the Ethernet header:

| Offset | Size | Field |
|---|---|---|
| 0 | 2 | Version, always `1` |
| 2 | 2 | Code |
| 4 | 6 | MAC: the discovery multicast address in a discovery request, the device MAC otherwise |
| 10 | 4 | Transaction ID, chosen by the requester and echoed in the reply |
| 14 | 20 | Authentication digest, all zero when bit `0x4000` of the code is clear |

ZON rejects frames whose ZDP payload is shorter than 35 bytes or whose version is not 1.

## Codes

| Code | Meaning | Authenticated | Source |
|---|---|---|---|
| `0x8000` | Discovery request | no | observed |
| `0x0002` | Query: read fields without a password | no | observed |
| `0x4004` | Get: read fields with a password | yes | observed |
| `0x4006` | Set: write fields or trigger actions | yes | observed (reboot) |

A WAC6503D-S given a set with Device reboot action (type 26) restarted without sending any reply.

Bit `0x4000` marks an authenticated request. Replies echo the transaction ID. A WAC6503D-S answered a get with `0x4005`, a signed reply, and every refusal with `0x00FF` plus an Error TLV. ZON's parser also accepts `0x0001` and `0x0003`. `zon.py` matches replies by transaction ID and source MAC.

## Fields (TLVs)

After the header comes a list of TLVs, terminated by type `0` with length `0`:

```
type (1 byte) | length (1 byte) | value (length bytes)
```

A request lists the fields it wants with length 0. The reply fills them in. Strings include their trailing NUL in the length.

ZON keeps a table of 49 field descriptors at `0x4b9720` (28 bytes each). Each one holds the type, the minimum and maximum length, a flag for each of the four operations, and the field name. The table below is that data; `d q g s` mark the operations a field is allowed in (discovery, query, get, set).

| Type | Name | Value | Ops |
|---|---|---|---|
| 1 | Error | 2 to 255 bytes, see below | |
| 2 | Capabilities | list of supported field types | d q g |
| 3 | System MAC | 6 bytes | d q g |
| 4 | Device Model | string | d q g |
| 5 | FW Version | string | d q g |
| 6 | DHCP Setting | 2 bytes | d q g s |
| 7 | IPv4 Address | 4 bytes | d q g s |
| 8 | IPv4 Netmask | 4 bytes | d q g s |
| 9 | IPv4 Gateway | 4 bytes | d q g s |
| 10 | IPv4 DNS Server 1 | 4 bytes | d q g s |
| 11 | IPv4 DNS Server 2 | 4 bytes | d q g s |
| 12 | IPv4 NTP Server 1 | 4 bytes | d q g s |
| 13 | IPv4 DNS Server 1 (Domain Name) | string | d q g s |
| 14 | SNMP GET Community | string | g s |
| 15 | SNMP SET Community | string | g s |
| 16 | SNMP TRAP Community | string | d q g s |
| 17 | SNMP TRAP Server1 | 4 bytes | d q g s |
| 18 | IPv4 Syslog server 1 | 4 bytes | d q g s |
| 19 | IPv4 Syslog server 1 (Domain Name) | string | d q g s |
| 20 | IPv4 Syslog server 2 | 4 bytes | d q g s |
| 21 | IPv4 Syslog server 2 (Domain Name) | string | d q g s |
| 22 | System Name | string | d q g s |
| 23 | Password | 4 to 255 bytes | s |
| 24 | Location | string | d q g s |
| 25 | DHCP renew action | empty | s |
| 26 | Device reboot action | empty | s |
| 27 | Locator LED flash | 2 bytes, 1 = on | s |
| 28 | Device IPv4 Web GUI URL | string | d q g |
| 29 | F/W Upgraded Protocol information 1 | string | d q g |
| 30 | F/W Upgraded Protocol information 2 | string | d q g |
| 31 | TFTP F/W Upgraded | string | s |
| 32 | Reboot After F/W upgrade OK | empty | s |
| 33 | Device Type | 2 bytes | d q g |
| 34 | Explicit Save | empty | s |
| 35 | AP Operational Mode | 2 bytes | d q g |
| 36 | Reset Configuration to Default | empty | s |
| 37 | Device Uptime | 4 bytes, seconds | d q g |
| 38 | DHCP Status | 2 bytes | d q g |
| 39 | Locator LED Control | 2 bytes | d q g s |
| 40 | Locator LED Setting | 4 bytes | d q g s |
| 41 | Serial Number | string | g |
| 42 | HW Version | string | d q g |
| 43 | Password Security | 1 or 2 bytes | d q g |
| 44 | Salt | string | d q |
| 45 | Nebula | 2 bytes | d q g s |
| 46 | AC Discovery | string | d q g s |
| 47 | Link-local IPv6 Address | 16 bytes | d q g |
| 48 | The latest Global Unicast IPv6 Address | 16 bytes | d q g s |

ZON stores the uptime it receives multiplied by 1000, so the device sends seconds (from code, `FUN_0040c760`). A WAC6503D-S reported 263186, which is about three days.

## Discovery (observed)

1. Send code `0x8000` to `01:a0:c5:11:11:11` asking for types 2, 3 and 4.
2. Every device answers with exactly those three fields. Capabilities (type 2) lists the field types the device supports, one byte each.
3. For each device, send a query (code `0x0002`) for the supported types that allow queries.

The discovery reply does not carry IP, firmware or name. Those come from step 3.

## Errors (observed, layout from code)

A device that refuses a request answers with an Error TLV instead of the fields:

| Offset | Size | Meaning |
|---|---|---|
| 0 | 1 | Error code |
| 1 | 1 | Type of the field that caused it |
| 2 | 2 | Extended error code, when the length is 3 or more |
| 4 | rest | Message string |

ZON logs it as `Error code = %d, tlv type = %d` (`FUN_004073a0`). Codes seen on WAX650S and WAC6503D-S access points:

| Code | Meaning (inferred from when it appears) |
|---|---|
| 1 | Authentication failed. The field byte is 0 |
| 2 | The device does not support this field |
| 4 | The device lists this field but will not return it in this request |

A device reports one refused field at a time and drops every other field in the request. The access points list type 12 (NTP server) in their capabilities and still reject it with `04 0c`. Asking again without the refused type works. The access points check the fields before the signature, so a request with a refused field never reaches authentication.

## Authentication (from code)

Get and set requests are signed. The client:

1. builds the whole ZDP payload (header and TLVs, up to and including the `00 00` terminator) with the 20-byte digest field set to zero,
2. computes HMAC-SHA1 over it with the device key,
3. writes the result into bytes 14 to 33.

I checked the HMAC by running ZON's own routine (`FUN_0041d220`) in an emulator: it matches standard HMAC-SHA1.

### Short frames (observed)

A payload under 46 bytes gets padded to the Ethernet minimum. The device strips the padding before checking the signature, so the signature covers the real bytes only, never the padding. ZON's parser does the same on frames of exactly 46 bytes.

What the padding contains matters, though. With the client on Wi-Fi, short signed requests that left the client unpadded were rejected with error 1, even with the right password; the bridge that padded them apparently did not use zeros. The same requests padded with zeros by the client, and signed before padding, were accepted by a WAC6503D-S. `zon.py` therefore always pads to 46 bytes itself.

The device key comes from the admin password and two fields the device publishes: Password Security (type 43) and Salt (type 44). The low byte of Password Security selects the mode and the high byte selects an IV. The WAC6503D-S access points I tested do not advertise types 43 and 44 and answer a query for them with error 2. They use mode 0, confirmed with a signed get.

ZON's `FUN_004057a0` (it logs as `GenDevCipher`) derives the key:

| Mode | Key |
|---|---|
| 0 | The password itself |
| 1, 2, 3 | A 32-character base64 string, see below |
| 4 | Lowercase hex SHA-256 of the raw Salt field (as received, NUL included) followed by the password |

### Modes 1 to 3

The Salt field holds a hex string. ZON drops its last byte, then reads the hex pairs from the end towards the start, so the ciphertext bytes come out reversed. It decrypts them with AES-256-CBC through Windows CryptoAPI:

- key: entry `mode` of a five-row table at `0x4b87e0` in `ZON.exe` (32 bytes)
- IV: the IV of entry `Password Security >> 8` in the same table (16 bytes)
- padding: PKCS#7

The plaintext is a decimal number; its low byte is the salt `s`. Then (`FUN_00402800`):

```
d1  = MD5(password || "zyxel" || s)
d2  = MD5(password || "zyxel" || s || d1)
buf = d1 || d2[0:5] || s || 0x01            (23 bytes)
buf = buf || (0xFF - sum(buf)) & 0xFF        (24 bytes)
key = base64(buf)                            (32 characters)
```

The table holds the same key and IV in rows 1 to 3. Rows 0 and 4 are not AES rows, but ZON still takes the IV from row `Password Security >> 8`, so a high byte of 0 selects the 16 bytes found in row 0. The values are in `zon.py` as `AES`.

## Password change (from code)

ZON's change-password flow (`OnWMChangePwd`):

1. Query types 43 and 44 again to get the current salt.
2. Derive the key from the current password, as above.
3. Build the new value of the Password field (type 23), depending on the mode (`FUN_00409110`):
   - mode 2: the 32-character string above, computed from the new password and a new random salt between 1 and 254 that differs from the old one. The device reads the new salt from inside the string.
   - mode 4: 8 random characters from `A-Z 0-9 a-z` as the new salt, then the new password, then a NUL.
   - modes 0, 1 and 3: the new password as plain bytes, no NUL.
4. Encrypt that value with RC4, using the current key as the RC4 key (`FUN_00416b70`).
5. Send it as a set (code `0x4006`) containing only type 23, signed with the current key.

## Open questions

- Reply codes for authenticated get and set. ZON accepts any code below `0x100` and also checks bit `0x4000` to verify a signature on the reply. `zon.py` does not verify reply signatures.
- Whether a set needs an Explicit Save (type 34) to survive a reboot. ZON has the field; I have not traced when it sends it.
- The firmware upgrade flow (types 29 to 32 and ZON's bundled TFTP server).

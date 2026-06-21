# Sushi

The name says it all — just like sushi rolls a bunch of ingredients into one tight package, Sushi rolls [Termux](https://termux.dev), [Communication Bridge Pro](https://play.google.com/store/apps/details?id=com.hardcodedjoy.commbridge), [Proxmark3](https://www.proxmark.com/), and the [Doppelganger Assistant](https://github.com/tweathers-sec/doppelganger_assistant) into a single mobile tool you can run from your phone.

It is a mobile RFID clone controller that bridges a [Doppelganger Core](https://github.com/tweathers-sec/Doppelganger_Core) reader with a Proxmark3 RDV4 (Blueshark BT) for on-the-move card cloning during authorized physical penetration tests. No laptop required.

> **For authorized security testing only.** Use only on systems and facilities you have explicit written permission to test.

---

## How it works

Sushi runs as a local web server (FastAPI) inside Termux or iSH on your phone. You open it in your mobile browser. It polls the Doppelganger Core over WiFi and drives the Proxmark3 over Bluetooth (via Communication Bridge Pro's TCP bridge). When a card is captured, you can clone or emulate it in one tap — or enable Auto-Clone to do it the moment the card appears.

```
Doppelganger Core  ──WiFi──>  [Sushi backend]  ──TCP──>  Communication Bridge Pro
                                      |                              |
                               Mobile browser                  Proxmark3 RDV4
                               (your phone)                   (Blueshark BT)
```

---

## Features

- **Real-time card list** — polls the Core's `/cards.csv` every second; new cards appear instantly with a visual badge
- **Auto-Clone mode** — when toggled on, every newly captured card is automatically written or emulated without any tap
- **Emulate mode** — starts a persistent emulation on the Proxmark3; stays active until you tap Stop
- **Write mode** — writes card data to a blank T5577 or compatible card in one shot
- **Per-card manual actions** — Write and Emulate buttons on every card for full manual control
- **Connection status** — live indicators for both the Doppelganger Core and Proxmark3
- **Activity log** — timestamped log of every pm3 command, result, and error
- **Persistent settings** — Core IP, PM3 device path, binary path, and poll interval saved to `sushi_config.json`
- **Self-contained** — all Python dependencies live in a local `.venv/`; nothing installs globally
- **Auto-update** — checks git remote on every launch and pulls new commits automatically; reinstalls deps if `requirements.txt` changed

---

## Supported card types

| Card type | pm3 format | Write | Emulate |
|-----------|-----------|-------|---------|
| HID Prox 26-bit | H10301 | Yes | Yes |
| HID Prox 27-bit | H10307 | Yes | Yes |
| HID 2804W 28-bit | 2804W | Yes | Yes |
| HID ATSW 30-bit | ATSW30 | Yes | Yes |
| HID ADT 31-bit | ADT31 | Yes | Yes |
| HID D10202 33-bit | D10202 | Yes | Yes |
| HID H10306 34-bit | H10306 | Yes | Yes |
| HID Corporate 1000 35-bit | C1k35s | Yes | Yes |
| HID S12906 36-bit | S12906 | Yes | Yes |
| HID H10304 37-bit | H10304 | Yes | Yes |
| HID H800002 46-bit | H800002 | Yes | Yes |
| HID Corporate 1000 48-bit | C1k48s | Yes | Yes |
| Avigilon 56-bit | Avig56 | Yes | Yes |
| EM4100 / EM4102 | 32-bit LF | Yes | Yes |
| AWID 26-bit | AWID | Yes | Yes |
| Indala 26/27/29-bit | Indala | Yes | Yes |
| MIFARE Classic | HF ISO14443A | Yes (UID) | Yes |
| iCLASS | HF iCLASS | Yes | No* |
| PIV / SEOS | HF ISO14443A | No | Yes |
| Paxton Net2 | HF iCLASS | Yes | No* |

\* iCLASS and Paxton emulation via pm3 CLI requires a pre-dumped card file, which Sushi does not currently manage.

---

## Requirements

**Hardware**
- Doppelganger Core (firmware 1.x or later)
- Proxmark3 RDV4 with Blueshark Bluetooth addon module
- Android phone with Termux, **or** iPhone/iPad with iSH

**Apps on your phone**
- [Communication Bridge Pro](https://play.google.com/store/apps/details?id=com.hardcodedjoy.commbridge) (Android) or the iOS equivalent — bridges Proxmark3 Bluetooth to a local TCP port
- A browser (Chrome, Firefox, Safari) — this is your UI

---

## Proxmark3 setup (RDV4 + Blueshark)

### Understanding "ARM firmware does not match"

This error means the firmware flashed onto the RDV4 was compiled at a different git revision than the client binary, **or** the firmware is missing the `PLATFORM_EXTRAS=BTADDON` flag required for Blueshark support. Both must match. The Termux pkg version (`pkg install proxmark3`) is the iceman fork and is fine as a client — the problem is almost always the firmware on the device.

The two required build settings for RDV4 + Blueshark:
```
PLATFORM=PM3RDV4
PLATFORM_EXTRAS=BTADDON
```

These go in a file called `Makefile.platform` in the proxmark3 source tree before building.

---

### Option A — Flash from a PC (recommended)

This is the most reliable path. On a PC/Mac with the iceman fork:

```bash
git clone https://github.com/RfidResearchGroup/proxmark3.git
cd proxmark3
printf 'PLATFORM=PM3RDV4\nPLATFORM_EXTRAS=BTADDON\n' > Makefile.platform
make -j$(nproc) fullimage
```

Put the RDV4 in bootloader mode (hold the side button while plugging in USB — LED turns solid red), then flash:

```bash
pm3 -p /dev/ttyACM0 --flash --image fullimage.elf
```

Then ensure your Termux client is from the same version by building client-only (see Option B, Phase 1) or using `pkg install proxmark3` if it matches the tagged release you built.

---

### Option B — Build everything in Termux (no PC needed)

Termux cannot cross-compile ARM firmware natively. Firmware compilation requires proot-distro with a Debian environment containing the ARM toolchain.

**Phase 1 — Client only (fast, ~5–10 min)**

```bash
pkg install git clang make cmake python libc++
git clone --depth=1 https://github.com/RfidResearchGroup/proxmark3.git ~/proxmark3
printf 'PLATFORM=PM3RDV4\nPLATFORM_EXTRAS=BTADDON\n' > ~/proxmark3/Makefile.platform
make -C ~/proxmark3 host -j$(nproc)
ln -sf ~/proxmark3/pm3 $PREFIX/bin/pm3
```

> Note: `make host` builds the client tools only — no ARM cross-compiler needed. Do **not** run plain `make` in Termux; it will fail trying to cross-compile firmware.

**Phase 2 — Firmware via proot-distro (~20–30 min)**

```bash
pkg install proot-distro
proot-distro install debian
proot-distro login debian --termux-home -- bash -c \
  "apt-get update && apt-get install -y gcc-arm-none-eabi libnewlib-dev libnewlib-arm-none-eabi && \
   make -C ~/proxmark3 -j\$(nproc) fullimage"
```

This produces `~/proxmark3/fullimage.elf`. Flash it with the device in bootloader mode:

```bash
pm3 -p tcp:localhost:4321 --flash --image ~/proxmark3/fullimage.elf
```

> **WARNING:** Only flash `fullimage`, never `bootrom`, from Android. A failed bootrom flash can brick the device.

**Sushi's "Install from source" button** runs both phases automatically and shows the exact flash command once the firmware is built.

---

### Verify the connection

With Communication Bridge Pro connected to the Blueshark and the correct firmware flashed:

```bash
pm3 -p tcp:localhost:4321 -c "hw version"
```

No mismatch warning = firmware and client match. Sushi's Connect PM3 should then turn the dot green.

> **TCP port format:** The iceman fork uses `tcp:localhost:4321` (single colon, no `//`). This is the default in Sushi's settings.

---

## Installation

### Android (Termux)

> Install Termux from [F-Droid](https://f-droid.org/packages/com.termux/), not the Play Store. The Play Store version is outdated and no longer maintained.

**1. Install Termux and grant storage permission**

```
Settings > Apps > Termux > Permissions > Storage > Allow
```

Then open Termux and run:

```bash
termux-setup-storage
pkg update -y
pkg install -y git python
```

**2. Clone Sushi**

```bash
git clone https://github.com/morganh83/sushi.git
cd sushi
```

**3. Make the launcher executable**

```bash
chmod +x sushi.sh
```

**4. First launch**

```bash
bash sushi.sh
```

On first run this creates a `.venv/` inside the project folder and installs all Python dependencies. It then starts the server on port `8080`.

**5. Open the UI**

Open your phone's browser and go to:

```
http://localhost:8080
```

---

### iOS (iSH)

> iSH runs an Alpine Linux shell in userspace. It does not require a jailbreak.

**1. Install iSH**

Get [iSH Shell](https://apps.apple.com/us/app/ish-shell/id1436902243) from the App Store.

**2. Install dependencies**

Open iSH and run:

```sh
apk update
apk add git python3 py3-pip bash curl
```

**3. Clone Sushi**

```sh
git clone https://github.com/morganh83/sushi.git
cd sushi
chmod +x sushi.sh
```

**4. First launch**

```sh
bash sushi.sh
```

iSH runs the server on `localhost:8080` inside its virtual network. To open the UI, use **iSH's built-in browser shortcut** or open Safari and navigate to:

```
http://localhost:8080
```

> iSH note: Python venv creation is slow on iOS due to the x86 emulation layer. The first launch may take 2–3 minutes while dependencies install. Subsequent launches are fast.

---

## Connection workflow

### Step 1 — Doppelganger Core

The recommended workflow keeps your phone's mobile data available and avoids switching WiFi networks mid-engagement.

1. **Enable your phone's mobile hotspot.**
2. **Power on the Doppelganger Core.** It will broadcast its own WiFi AP (`doppelganger_XXXX`, password `UndertheRadar`).
3. **Connect your phone to the Core's AP** and open `http://192.168.4.1` in a browser.
4. **Configure the Core to join your hotspot** via the network/WiFi settings page. Once saved, the Core reboots and joins your hotspot as a client.
5. **Reconnect your phone to your hotspot** (or it may reconnect automatically). The Core is now on the same network as your phone.
6. **In Sushi → Settings**, tap **Scan** next to the Core IP field. Sushi probes the hotspot subnet and finds the Core automatically. Tap the result to save the IP.

The CORE dot turns green once Sushi can reach the Core.

### Step 2 — Proxmark3

Connect the Proxmark3 however you normally do for your hardware configuration:

**Blueshark (Bluetooth, most common)**

1. Open **Communication Bridge Pro**, connect to the Blueshark module, and confirm it is bridging on port **4321**.
2. That is all — Sushi talks to CBP's TCP port directly. No further setup needed.

**USB (via Termux)**

If using USB instead of Bluetooth, set the PM3 device path in Settings to the serial device (e.g. `/dev/ttyACM0`) and connect the Proxmark3 via USB OTG.

The PM3 dot turns green once the TCP port is reachable (Bluetooth) or the device file is present (USB).

---

## First-time settings

Open **Settings** (gear icon, top right) after the first launch.

| Setting | What to enter |
|---------|--------------|
| **Doppelganger Core IP** | Set automatically by the Scan button (see above) |
| **CBP TCP Port** | `tcp:localhost:4321` (default — matches Communication Bridge Pro's default port) |
| **Poll Interval** | How often Sushi checks for new cards. Default `1.0` s |

Tap **Save**, then tap **Test PM3 Connection** to run a full `hw version` check and confirm the Proxmark3 is reachable end-to-end.

---

## Usage

### Auto-Clone (hands-free)

1. Confirm both status dots (CORE and PM3) are green
2. Place a blank T5577 (LF) or writable HF card in front of the Proxmark3 antenna
3. Toggle **Auto-Clone** on
4. Select **Write** or **Emulate** mode
5. Walk near a target reader — when the Core captures a card, Sushi fires the proxmark3 command automatically and vibrates the phone

### Manual clone

Each card in the list has two buttons:

- **Write** — writes the card data to a blank card held against the Proxmark3 antenna (one-shot, proxmark3 exits when done)
- **Emulate** — starts continuous emulation; the Proxmark3 acts as that card until you tap **Stop**

### Stopping emulation

When emulation is active, a **Stop** button appears in the toolbar and on the card row. Tap either to terminate the emulation process.

### Resetting the "New" markers

Tap **Reset New** (top of the card list) to clear the orange NEW badges. Useful after reviewing captures so the next real new card stands out.

### Running without an internet connection

```bash
bash sushi.sh --no-update
```

Skips the git update check entirely. Use this when your phone is on the Core's isolated WiFi AP with no internet.

### Resetting the virtual environment

If you upgrade Python or the venv becomes corrupted:

```bash
rm -rf .venv
bash sushi.sh
```

### Custom port

```bash
bash sushi.sh --port 9090
```

---

## Updating

Updates happen automatically on each launch. If you want to trigger one manually without launching the app:

```bash
git pull
```

If `requirements.txt` changed, rebuild the venv:

```bash
rm -rf .venv && bash sushi.sh
```

---

## File reference

```
sushi/
├── sushi.sh              Launch script (auto-update + venv bootstrap)
├── main.py               FastAPI app, WebSocket hub, poll loop
├── doppelganger.py       Doppelganger Core HTTP client and CSV parser
├── proxmark.py           Proxmark3 subprocess manager
├── card_commands.py      Card type → pm3 command mapping
├── config.py             Settings loader/saver
├── pyproject.toml        Project metadata and dependency versions
├── requirements.txt      Pinned pip dependencies
├── .gitignore
└── static/
    └── index.html        Mobile web UI (single file, no build step)
```

`sushi_config.json` is created on first settings save and is gitignored — it holds per-device settings that differ between operators.

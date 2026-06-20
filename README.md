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

## First-time setup

After the UI loads, tap the gear icon (top right) to open Settings.

| Setting | What to enter |
|---------|--------------|
| **Doppelganger Core IP** | `192.168.4.1` if your phone is connected to the Core's WiFi AP, or the IP the Core gets on your phone's hotspot |
| **PM3 Device / Port** | The TCP address Communication Bridge Pro exposes, e.g. `tcp://localhost:2323` — check CBP settings for the exact port |
| **PM3 Binary Path** | Leave as `pm3` if it is in your PATH; otherwise provide the full path, e.g. `/data/data/com.termux/files/home/proxmark3/pm3` |
| **Poll Interval** | How often (seconds) to check for new cards. Default `1.0` |

Tap **Save**, then tap **Test PM3 Connection** to confirm the Proxmark3 is reachable.

---

## Connecting to the Doppelganger Core

The Core can connect to your phone in two ways:

**Option A — Join the Core's WiFi AP (simplest)**

The Core broadcasts a network named `doppelganger_XXXX`. Connect your phone to it (password: `UndertheRadar`). The Core is then at `192.168.4.1`. Note: while connected to the Core's AP, your phone has no internet access.

**Option B — Core joins your phone's hotspot**

Enable your phone's mobile hotspot, then configure the Core to connect to it via its web UI (`http://192.168.4.1/config.html` while on the Core's AP). Once joined, find the IP the Core was assigned by your hotspot's DHCP, and enter that in Sushi's settings. This leaves your phone's mobile data available.

---

## Usage

### Auto-Clone (hands-free)

1. Confirm both status dots (CORE and PM3) are green
2. Place a blank T5577 (LF) or writable HF card in front of the Proxmark3 antenna
3. Toggle **Auto-Clone** on
4. Select **Write** or **Emulate** mode
5. Walk near a target reader — when the Core captures a card, Sushi fires the pm3 command automatically

### Manual clone

Each card in the list has two buttons:

- **Write** — writes the card data to the blank card on the Proxmark3 antenna (one-shot)
- **Emulate** — starts continuous emulation; the Proxmark3 acts as that card until you tap **Stop**

### Stopping emulation

When emulation is active, a **Stop** button appears in the toolbar and on the card row. Tap either to terminate the pm3 process.

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

"""
Proxmark3 client — one-shot command mode.

Each write/read command spawns proxmark3 as a short-lived subprocess
(proxmark3 -p <device> -c "<command>").  This is simpler and more reliable
than a persistent interactive session because there is no timing dependency
on when pm3 shows its prompt.

Emulation is the only case that keeps a long-running subprocess alive,
since the sim command by design runs until interrupted.

PM3 status (the indicator dot) is a plain TCP socket check on CBP's port —
no pm3 process needed just to know whether the bridge is reachable.
"""

import asyncio
import re
import shutil
import socket
from pathlib import Path
from typing import Optional

ANSI = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _clean(raw: bytes | str) -> str:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    return ANSI.sub("", raw)


def detect_binary() -> str:
    """Find the proxmark3 ELF binary, preferring direct paths over wrapper scripts.

    The pm3 file in PATH is a shell script that internally calls the proxmark3
    ELF binary.  If proxmark3 is not in PATH (e.g. after pkg uninstall), the
    script fails with 'PROXMARK3: command not found'.  We therefore look for
    the ELF binary directly before falling back to whatever is in PATH.

    Priority:
      1. ~/proxmark3/client/proxmark3  — source build (ELF, full path)
      2. proxmark3 in PATH             — pkg install or manual link
      3. pm3 in PATH                   — shell wrapper (last resort)
    """
    src = Path.home() / "proxmark3" / "client" / "proxmark3"
    if src.exists():
        return str(src)

    for name in ("proxmark3", "pm3"):
        path = shutil.which(name)
        if path:
            return path

    return ""


class ProxmarkClient:
    def __init__(self, config) -> None:
        self.config = config
        self.binary: str = detect_binary()
        self._sim_proc: Optional[asyncio.subprocess.Process] = None
        self._sim_card: Optional[dict] = None

    def redetect(self) -> str:
        self.binary = detect_binary()
        return self.binary

    # ── State ─────────────────────────────────────────────────────────────

    @property
    def is_emulating(self) -> bool:
        return self._sim_proc is not None and self._sim_proc.returncode is None

    @property
    def emulating_card(self) -> Optional[dict]:
        return self._sim_card if self.is_emulating else None

    # ── CBP port check (lightweight — no pm3 subprocess) ─────────────────

    async def port_open(self) -> bool:
        """Return True if Communication Bridge Pro's TCP port is reachable."""
        try:
            port = int(str(self.config.pm3_device).rsplit(":", 1)[-1])
        except (ValueError, IndexError):
            return False
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        loop = asyncio.get_event_loop()
        try:
            await asyncio.wait_for(
                loop.sock_connect(sock, ("127.0.0.1", port)), timeout=1.0
            )
            return True
        except Exception:
            return False
        finally:
            try:
                sock.close()
            except Exception:
                pass

    async def ping(self) -> bool:
        return await self.port_open()

    # ── Write verification ────────────────────────────────────────────────

    async def verify_write(self, card: dict) -> dict:
        """
        Read the card back immediately after writing and compare to expected
        values.  The card must stay on the Proxmark3 antenna during verification.

        Returns dict with keys:
          verified: True | False | None (None = unsupported / unreadable)
          details:  human-readable comparison string
          output:   raw proxmark3 output
        """
        card_type = str(card.get("card_type", "hid")).lower()
        bl = int(card.get("bl") or 0)
        exp_fc  = str(card.get("fc",  "")).strip()
        exp_cn  = str(card.get("cn",  "")).strip()
        exp_hex = str(card.get("hex", "")).replace(" ", "").replace("0x", "").upper()

        # Choose the correct reader command
        if card_type == "em4100" or bl == 32:
            cmd = "lf em 410x reader"
        elif card_type == "awid":
            cmd = "lf awid reader"
        elif card_type == "indala":
            cmd = "lf indala reader"
        elif card_type in ("mifare",):
            cmd = "hf mf info"
        elif card_type in ("iclass", "paxton"):
            return {"verified": None, "details": "Verification not supported for this card type.", "output": ""}
        else:
            cmd = "lf hid reader"   # covers all HID/Wiegand variants

        result = await self.run_command(cmd, timeout=12.0)
        output = result["output"]

        # Parse FC / CN from output (HID-family)
        fc_match  = re.search(r'FC\s*[=:]\s*(\d+)', output, re.IGNORECASE)
        cn_match  = re.search(r'(?:Card|CN|Card\s+Number)\s*[=:]\s*(\d+)', output, re.IGNORECASE)
        hex_match = re.search(r'ID\s*[=:]\s*([0-9a-fA-F]+)', output, re.IGNORECASE)

        if card_type == "em4100" or bl == 32:
            if hex_match:
                got = hex_match.group(1).upper()
                ok  = got == exp_hex
                return {"verified": ok, "output": output,
                        "details": f"Expected {exp_hex}, read {got}"}
            return {"verified": False, "output": output,
                    "details": "Could not read EM4100 ID from card"}

        if fc_match and cn_match:
            got_fc = fc_match.group(1)
            got_cn = cn_match.group(1)
            ok     = (got_fc == exp_fc and got_cn == exp_cn)
            return {"verified": ok, "output": output,
                    "details": f"Expected FC:{exp_fc} CN:{exp_cn} — read FC:{got_fc} CN:{got_cn}"}

        return {"verified": False, "output": output,
                "details": "Card did not respond or output format not recognised — keep card on antenna and try again"}

    # ── One-shot command ──────────────────────────────────────────────────

    async def run_command(self, cmd: str, timeout: float = 30.0) -> dict:
        """Spawn proxmark3, run one command, return output."""
        if not self.binary:
            return {
                "success": False,
                "output": "proxmark3 binary not found. Run 'pkg install proxmark3' in Termux.",
                "returncode": -1,
            }
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary, "-p", self.config.pm3_device, "-c", cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = _clean(stdout)
            return {
                "success": proc.returncode == 0,
                "output": output,
                "returncode": proc.returncode,
            }
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return {"success": False, "output": "Command timed out.", "returncode": -1}
        except FileNotFoundError:
            self.binary = ""
            return {"success": False, "output": "proxmark3 binary not found.", "returncode": -1}
        except Exception as e:
            return {"success": False, "output": str(e), "returncode": -1}

    # ── Emulation (long-running subprocess) ───────────────────────────────

    async def start_emulation(self, cmd: str, card: dict) -> dict:
        await self.stop_emulation()
        if not self.binary:
            return {"success": False, "output": "proxmark3 binary not found.", "returncode": -1}
        try:
            self._sim_proc = await asyncio.create_subprocess_exec(
                self.binary, "-p", self.config.pm3_device, "-c", cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self._sim_card = card
            await asyncio.sleep(2.0)
            if self._sim_proc.returncode is not None:
                raw = await self._sim_proc.stdout.read()
                self._sim_card = None
                return {"success": False, "output": _clean(raw), "returncode": self._sim_proc.returncode}
            return {"success": True, "output": "Emulation running."}
        except Exception as e:
            self._sim_card = None
            return {"success": False, "output": str(e), "returncode": -1}

    async def stop_emulation(self) -> dict:
        was = self._sim_card
        self._sim_card = None
        if self._sim_proc and self._sim_proc.returncode is None:
            try:
                self._sim_proc.terminate()
                await asyncio.wait_for(self._sim_proc.wait(), timeout=3.0)
            except Exception:
                try:
                    self._sim_proc.kill()
                except Exception:
                    pass
        self._sim_proc = None
        return {"success": True, "was_emulating": was is not None}

    # ── Version info (no device connection needed) ────────────────────────

    async def get_client_version(self) -> str:
        home = Path.home()
        candidates: list[str] = []
        src = home / "proxmark3" / "client" / "proxmark3"
        if src.exists():
            candidates.append(str(src))
        pkg = shutil.which("proxmark3")
        if pkg:
            candidates.append(pkg)
        if not candidates and self.binary:
            candidates.append(self.binary)
        if not candidates:
            return "No binary found. Run 'pkg install proxmark3'."

        for binary in candidates:
            for flag in ("-v", "--version", "-h", "--help"):
                try:
                    proc = await asyncio.create_subprocess_exec(
                        binary, flag,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                    )
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
                    text = _clean(stdout).strip()
                    lines = [
                        l for l in text.splitlines()
                        if l.strip()
                        and "Script cannot access" not in l
                        and "insufficient privileges" not in l
                    ]
                    if lines:
                        return f"[{Path(binary).name} {flag}]\n" + "\n".join(lines[:10])
                except Exception:
                    continue
        return "Could not get version info. Try 'proxmark3 -v' in Termux."


# ── PM3 Continuous Reader ──────────────────────────────────────────────────────

class PM3Reader:
    """
    Manages long-running pm3 subprocesses for continuous card reading.

    LF mode:  `lf hid reader -@`  → outputs FC/CN/BL/Format directly (clone-ready)
    HF modes: per-protocol readers that capture UID/CSN for identification;
              cloning HF cards then requires a separate one-shot dump command.
    """

    COOLDOWN = 5.0   # seconds before same card ID can trigger again

    LF_CMD = "lf hid reader -@"

    HF_CMDS: dict[str, str] = {
        "iclass":  "hf iclass reader -@",
        "mifare":  "hf 14a reader -@",
        "desfire": "hf mfdes info -@",
        "15693":   "hf 15 reader -@",
    }

    DUMP_CMDS: dict[str, str] = {
        "iclass": "hf iclass dump --ki 0",
        "mifare": "hf mf autopwn",
        "15693":  "hf 15 dump",
        # desfire intentionally omitted — needs session keys
    }

    def __init__(self, config, binary_fn, on_card) -> None:
        self.config    = config
        self._binary   = binary_fn   # callable → str
        self._on_card  = on_card     # async (card: dict) -> None
        self._tasks: list[asyncio.Task] = []
        self._procs: list[asyncio.subprocess.Process] = []
        self._cooldown: dict[str, float] = {}
        self._lf_active = False
        self._hf_type   = ""

    # ── Public state ──────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return bool(self._tasks) and any(not t.done() for t in self._tasks)

    @property
    def lf_active(self) -> bool:
        return self._lf_active and self.is_running

    @property
    def hf_type(self) -> str:
        return self._hf_type if self.is_running else ""

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self, lf: bool = False, hf_type: str = "") -> dict:
        await self.stop()
        binary = self._binary()
        if not binary:
            return {"success": False, "error": "proxmark3 binary not found"}

        self._lf_active = lf
        self._hf_type   = hf_type

        if lf:
            self._tasks.append(asyncio.create_task(
                self._run_reader(self.LF_CMD, "lf")
            ))
        if hf_type and hf_type in self.HF_CMDS:
            self._tasks.append(asyncio.create_task(
                self._run_reader(self.HF_CMDS[hf_type], hf_type)
            ))

        modes = []
        if lf:
            modes.append("LF HID")
        if hf_type:
            modes.append(f"HF {hf_type}")

        return {"success": True, "modes": modes}

    async def stop(self) -> None:
        self._lf_active = False
        self._hf_type   = ""
        for t in self._tasks:
            t.cancel()
        for p in self._procs:
            try:
                p.kill()
            except Exception:
                pass
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._procs.clear()

    # ── Streaming reader ──────────────────────────────────────────────────

    async def _run_reader(self, cmd: str, mode: str) -> None:
        binary = self._binary()
        if not binary:
            return
        import logging
        log = logging.getLogger("sushi.pm3reader")
        try:
            proc = await asyncio.create_subprocess_exec(
                binary, "-p", self.config.pm3_device, "-c", cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self._procs.append(proc)
            buf = ""
            async for chunk in proc.stdout:
                line = _clean(chunk)
                buf += line
                # A new scan cycle starts on separator or success line
                if "[=] ----" in buf or ("[+]" in line and len(buf) > 200):
                    card = self._parse_block(buf, mode)
                    if card:
                        await self._fire(card)
                    buf = line  # keep current line as start of next block
                # Bound the buffer
                if len(buf) > 8000:
                    buf = buf[-2000:]
            # Parse any remaining buffer
            card = self._parse_block(buf, mode)
            if card:
                await self._fire(card)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error("PM3Reader %s error: %s", mode, e)
        finally:
            try:
                self._procs.remove(proc)
            except Exception:
                pass

    async def _fire(self, card: dict) -> None:
        """Check cooldown then call the on_card callback."""
        import time
        cid  = card.get("id", "")
        now  = time.monotonic()
        last = self._cooldown.get(cid, 0.0)
        if now - last >= self.COOLDOWN:
            self._cooldown[cid] = now
            await self._on_card(card)

    # ── Parsing ───────────────────────────────────────────────────────────

    def _parse_block(self, buf: str, mode: str) -> Optional[dict]:
        from doppelganger import parse_kv_row

        if mode == "lf":
            # pm3 LF HID output: same key:value fields as the Doppelganger Core CSV
            # Split on newlines and treat each "Key : Value" line as a column
            lines = [l.strip() for l in buf.splitlines() if ": " in l and "[" in l]
            if not lines:
                return None
            # Strip pm3 prefix markers ([=], [+], etc.) from each line
            cleaned = []
            for l in lines:
                # Remove "[=] " or "[+] " prefix
                if "] " in l:
                    l = l.split("] ", 1)[1]
                cleaned.append(l)
            card = parse_kv_row(cleaned)
            if card:
                card["source"] = "pm3"
                card["scan_count"] = 1
            return card

        # HF modes — parse UID / CSN
        uid_match = re.search(
            r'(?:UID|CSN)\s*[=:]\s*([0-9a-fA-F](?:[0-9a-fA-F ])*[0-9a-fA-F])',
            buf, re.IGNORECASE,
        )
        if not uid_match:
            return None

        uid = uid_match.group(1).replace(" ", "").upper()
        type_map = {
            "iclass":  ("iclass",  f"iclass-{uid}"),
            "mifare":  ("mifare",  f"mifare-{uid}"),
            "desfire": ("desfire", f"desfire-{uid}"),
            "15693":   ("15693",   f"15693-{uid}"),
        }
        card_type, card_id = type_map.get(mode, ("hf", f"hf-{uid}"))
        return {
            "card_type": card_type,
            "bl": "", "fc": "", "cn": "",
            "hex": uid, "bin": "", "format": card_type.upper(),
            "id": card_id,
            "source": "pm3",
            "scan_count": 1,
        }

    # ── One-shot dump ─────────────────────────────────────────────────────

    async def dump(self, card: dict, proxmark_client) -> dict:
        """Run the appropriate dump command for an HF card."""
        card_type = card.get("card_type", "")
        cmd = self.DUMP_CMDS.get(card_type)
        if not cmd:
            return {"success": False, "output": f"No dump command for {card_type}",
                    "card_type": card_type}
        result = await proxmark_client.run_command(cmd, timeout=120.0)
        return {**result, "card_type": card_type}

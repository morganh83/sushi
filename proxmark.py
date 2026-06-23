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

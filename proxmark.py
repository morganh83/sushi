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
    """Return 'proxmark3' or 'pm3' if found in PATH, else ''.

    proxmark3 is the actual ELF binary; pm3 is a shell wrapper that scans
    /dev/tty* on startup, which fails without root on Android.
    Since we always pass -p, either binary works — but proxmark3 is safer.
    """
    for name in ("proxmark3", "pm3"):
        if shutil.which(name):
            return name
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

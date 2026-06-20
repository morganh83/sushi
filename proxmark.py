"""
Proxmark3 client using a persistent interactive pm3 session.

One pm3 process is kept running and reused for all commands, so
Communication Bridge Pro only sees a single connection rather than a
new connect/disconnect for every card operation.
"""

import asyncio
import re
import shutil
from typing import Optional

ANSI = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
PM3_PROMPT = b"pm3 --> "


def _clean(raw: bytes | str) -> str:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    return ANSI.sub("", raw)


def detect_binary() -> str:
    """Return 'pm3' or 'proxmark3' if found in PATH, else ''."""
    for name in ("pm3", "proxmark3"):
        if shutil.which(name):
            return name
    return ""


class ProxmarkClient:
    def __init__(self, config) -> None:
        self.config = config
        self.binary: str = detect_binary()
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._lock = asyncio.Lock()
        self._connected = False
        self._sim_active = False
        self._sim_card: Optional[dict] = None

    # ── Public state ──────────────────────────────────────────────────────

    def redetect(self) -> str:
        self.binary = detect_binary()
        return self.binary

    @property
    def is_connected(self) -> bool:
        return (self._connected
                and self._proc is not None
                and self._proc.returncode is None)

    @property
    def is_emulating(self) -> bool:
        return self._sim_active and self.is_connected

    @property
    def emulating_card(self) -> Optional[dict]:
        return self._sim_card if self.is_emulating else None

    # ── Connection lifecycle ──────────────────────────────────────────────

    async def connect(self) -> dict:
        """Start a persistent pm3 session and wait for the first prompt."""
        await self.disconnect()
        if not self.binary:
            return {"success": False, "error": "pm3 binary not found in PATH."}
        try:
            self._proc = await asyncio.create_subprocess_exec(
                self.binary, "-p", self.config.pm3_device,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            # Drain banner text until the first prompt
            await asyncio.wait_for(self._drain_to_prompt(), timeout=15.0)
            self._connected = True
            return {"success": True}
        except asyncio.TimeoutError:
            await self.disconnect()
            return {"success": False,
                    "error": "Timed out waiting for pm3 prompt — is Communication Bridge Pro connected to the Blueshark?"}
        except FileNotFoundError:
            self.binary = ""
            return {"success": False, "error": f"Binary not found: {self.binary}"}
        except Exception as e:
            await self.disconnect()
            return {"success": False, "error": str(e)}

    async def disconnect(self) -> None:
        """Gracefully shut down the pm3 session."""
        self._connected = False
        self._sim_active = False
        self._sim_card = None
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.stdin.write(b"quit\n")
                await self._proc.stdin.drain()
                await asyncio.wait_for(self._proc.wait(), timeout=3.0)
            except Exception:
                try:
                    self._proc.kill()
                    await self._proc.wait()
                except Exception:
                    pass
        self._proc = None

    def check_alive(self) -> bool:
        """Returns True if the session is still alive, updates state if it died."""
        if self._connected and (self._proc is None or self._proc.returncode is not None):
            self._connected = False
            self._sim_active = False
            self._sim_card = None
            return False
        return self._connected

    # ── Internal I/O ─────────────────────────────────────────────────────

    async def _drain_to_prompt(self) -> str:
        """Read stdout until pm3 prompt appears. Returns everything read."""
        try:
            data = await self._proc.stdout.readuntil(PM3_PROMPT)
            return _clean(data)
        except asyncio.IncompleteReadError as e:
            # Stream closed before prompt — process probably died
            self._connected = False
            return _clean(e.partial)

    # ── Commands ──────────────────────────────────────────────────────────

    async def run_command(self, cmd: str, timeout: float = 30.0) -> dict:
        """Send one command and return its output (blocks until next prompt)."""
        if not self.is_connected:
            return {"success": False, "output": "PM3 not connected.", "returncode": -1}
        async with self._lock:
            try:
                self._proc.stdin.write(f"{cmd}\n".encode())
                await self._proc.stdin.drain()
                output = await asyncio.wait_for(self._drain_to_prompt(), timeout=timeout)
                alive = self._proc.returncode is None
                if not alive:
                    self._connected = False
                return {"success": alive, "output": output,
                        "returncode": 0 if alive else -1}
            except asyncio.TimeoutError:
                self._connected = False
                await self.disconnect()
                return {"success": False, "output": "Command timed out.", "returncode": -1}
            except Exception as e:
                self._connected = False
                return {"success": False, "output": str(e), "returncode": -1}

    async def start_emulation(self, cmd: str, card: dict) -> dict:
        """Send a sim command without waiting for completion (runs until stopped)."""
        if not self.is_connected:
            return {"success": False, "output": "PM3 not connected.", "returncode": -1}
        async with self._lock:
            try:
                self._proc.stdin.write(f"{cmd}\n".encode())
                await self._proc.stdin.drain()
                self._sim_active = True
                self._sim_card = card
                await asyncio.sleep(0.5)  # let pm3 start the emulation
                return {"success": True, "output": "Emulation started."}
            except Exception as e:
                self._sim_active = False
                self._sim_card = None
                return {"success": False, "output": str(e), "returncode": -1}

    async def stop_emulation(self) -> dict:
        """Interrupt a running sim by sending Enter, then wait for prompt."""
        was = self._sim_card
        self._sim_active = False
        self._sim_card = None
        if self.is_connected:
            async with self._lock:
                try:
                    self._proc.stdin.write(b"\n")
                    await self._proc.stdin.drain()
                    await asyncio.wait_for(self._drain_to_prompt(), timeout=5.0)
                except Exception:
                    pass
        return {"success": True, "was_emulating": was is not None}

    async def ping(self) -> bool:
        """Returns True if connected and hw version responds."""
        if not self.is_connected:
            return False
        result = await self.run_command("hw version", timeout=10.0)
        return result["success"] or "PROXMARK" in result.get("output", "").upper()

    # ── Version / install helpers ─────────────────────────────────────────

    async def get_client_version(self) -> str:
        """Run the binary with --help to extract version info (no connection needed)."""
        if not self.binary:
            return "pm3 binary not found."
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary, "--help",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            lines = [l for l in _clean(stdout).splitlines() if l.strip()][:10]
            return "\n".join(lines)
        except Exception as e:
            return str(e)

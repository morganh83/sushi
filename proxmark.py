"""
Proxmark3 client using a persistent interactive pm3 session.

One pm3 process is kept running and reused for all commands, so
Communication Bridge Pro only sees a single connection rather than a
new connect/disconnect for every card operation.
"""

import asyncio
import re
import shutil
from pathlib import Path
from typing import Optional

ANSI = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


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
        """Start a persistent pm3 session and wait for the interactive prompt."""
        await self.disconnect()
        if not self.binary:
            return {"success": False, "error": "pm3 binary not found in PATH.", "banner": ""}
        try:
            self._proc = await asyncio.create_subprocess_exec(
                self.binary, "-p", self.config.pm3_device,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            banner = await asyncio.wait_for(self._drain_to_prompt(), timeout=15.0)
            self._connected = True
            return {"success": True, "banner": banner}
        except asyncio.TimeoutError:
            # Grab whatever output arrived before timing out so user can diagnose
            partial = ""
            if self._proc:
                try:
                    raw = await asyncio.wait_for(self._proc.stdout.read(4096), timeout=0.5)
                    partial = _clean(raw)
                except Exception:
                    pass
            await self.disconnect()
            return {"success": False, "banner": partial,
                    "error": "Timed out — is Communication Bridge Pro running and connected to Blueshark?"}
        except FileNotFoundError:
            self.binary = ""
            return {"success": False, "banner": "", "error": "Binary not found."}
        except Exception as e:
            await self.disconnect()
            return {"success": False, "banner": "", "error": str(e)}

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
        """
        Read until pm3 shows its interactive prompt.

        Handles two scenarios automatically:
        - ANSI colour codes interleaved in the prompt text (searches cleaned text)
        - Firmware mismatch confirmation prompt — auto-answers 'y' so pm3
          continues to the prompt instead of waiting for manual input
        """
        buf = b""
        answered_yn = False
        while True:
            try:
                chunk = await asyncio.wait_for(
                    self._proc.stdout.read(4096), timeout=1.0
                )
                if not chunk:
                    break
                buf += chunk
                cleaned = _clean(buf)

                # Auto-answer firmware mismatch "continue? [y/n]" prompt
                if not answered_yn and "[y/n]" in cleaned.lower():
                    self._proc.stdin.write(b"y\n")
                    await self._proc.stdin.drain()
                    answered_yn = True

                if "pm3 -->" in cleaned:
                    break
            except asyncio.TimeoutError:
                if buf:
                    break
                # No output yet — keep waiting (outer timeout handles limit)
        return _clean(buf)

    # ── Commands ──────────────────────────────────────────────────────────

    async def _ensure_connected(self) -> dict | None:
        """If not connected, try once to reconnect. Returns error dict or None."""
        if not self.is_connected:
            result = await self.connect()
            if not result["success"]:
                return {"success": False,
                        "output": f"PM3 not connected and reconnect failed: {result['error']}",
                        "returncode": -1}
        return None

    async def run_command(self, cmd: str, timeout: float = 30.0) -> dict:
        """Send one command and return its output (blocks until next prompt)."""
        err = await self._ensure_connected()
        if err:
            return err
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
        err = await self._ensure_connected()
        if err:
            return err
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
        """
        Get client version using the proxmark3 binary directly.

        The pm3 launcher is a shell script that scans /dev/tty* when no -p port
        is given, which requires root on Android and fails with a privileges error.
        The underlying proxmark3 ELF binary does not do device scanning and works
        fine for version checks without a device connection.
        """
        home = Path.home()

        # Prefer the actual ELF binary, not the pm3 wrapper script
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
            return "No binary found. Run 'pkg install proxmark3' or use the install button."

        for binary in candidates:
            for flag in ("-v", "--version"):
                try:
                    proc = await asyncio.create_subprocess_exec(
                        binary, flag,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                    )
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
                    text = _clean(stdout).strip()
                    # Filter out the pm3 script's privileges error in case it slips through
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

        return "Could not get version info. Try 'proxmark3 -v' directly in Termux."

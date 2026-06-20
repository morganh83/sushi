import asyncio
import re
import shutil
from typing import Optional

ANSI = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _clean(raw: bytes) -> str:
    return ANSI.sub("", raw.decode("utf-8", errors="replace"))


def detect_binary() -> str:
    """Return the first of 'pm3' or 'proxmark3' found in PATH, or '' if neither."""
    for name in ("pm3", "proxmark3"):
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

    # ── State ──────────────────────────────────────────────────────────────
    @property
    def is_emulating(self) -> bool:
        return self._sim_proc is not None and self._sim_proc.returncode is None

    @property
    def emulating_card(self) -> Optional[dict]:
        return self._sim_card if self.is_emulating else None

    # ── One-shot command ──────────────────────────────────────────────────
    async def run_command(self, cmd: str, timeout: float = 30.0) -> dict:
        if not self.binary:
            return {
                "output": "proxmark3 not found. Install pm3 or proxmark3 and ensure it is in PATH.",
                "returncode": -1, "success": False,
            }
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary, "-p", self.config.pm3_device, "-c", cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = _clean(stdout)
            return {"output": output, "returncode": proc.returncode,
                    "success": proc.returncode == 0}
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return {"output": "Command timed out.", "returncode": -1, "success": False}
        except FileNotFoundError:
            self.binary = ""
            return {"output": f"Binary not found. Is pm3 or proxmark3 installed and in PATH?",
                    "returncode": -1, "success": False}
        except Exception as e:
            return {"output": str(e), "returncode": -1, "success": False}

    # ── Long-running emulation ─────────────────────────────────────────────
    async def start_emulation(self, cmd: str, card: dict) -> dict:
        await self.stop_emulation()
        if not self.binary:
            return {"output": "proxmark3 not found.", "returncode": -1, "success": False}
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
                return {"output": _clean(raw), "returncode": self._sim_proc.returncode,
                        "success": False}
            return {"output": "Emulation running.", "returncode": 0, "success": True}
        except Exception as e:
            self._sim_card = None
            return {"output": str(e), "returncode": -1, "success": False}

    async def stop_emulation(self) -> dict:
        was = self._sim_card
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
        self._sim_card = None
        return {"success": True, "was_emulating": was is not None}

    async def ping(self) -> bool:
        result = await self.run_command("hw version", timeout=10.0)
        return result["success"] or "PROXMARK" in result["output"].upper()

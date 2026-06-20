import asyncio
import re
from typing import Optional

ANSI = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _clean(raw: bytes) -> str:
    return ANSI.sub("", raw.decode("utf-8", errors="replace"))


class ProxmarkClient:
    def __init__(self, config) -> None:
        self.config = config
        self._sim_proc: Optional[asyncio.subprocess.Process] = None
        self._sim_card: Optional[dict] = None

    # ── State ──────────────────────────────────────────────────────────────
    @property
    def is_emulating(self) -> bool:
        return self._sim_proc is not None and self._sim_proc.returncode is None

    @property
    def emulating_card(self) -> Optional[dict]:
        return self._sim_card if self.is_emulating else None

    # ── One-shot command (write / hw version / etc.) ───────────────────────
    async def run_command(self, cmd: str, timeout: float = 30.0) -> dict:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.config.pm3_path, "-p", self.config.pm3_device, "-c", cmd,
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
            return {"output": f"pm3 binary not found: {self.config.pm3_path}",
                    "returncode": -1, "success": False}
        except Exception as e:
            return {"output": str(e), "returncode": -1, "success": False}

    # ── Long-running emulation ─────────────────────────────────────────────
    async def start_emulation(self, cmd: str, card: dict) -> dict:
        await self.stop_emulation()
        try:
            self._sim_proc = await asyncio.create_subprocess_exec(
                self.config.pm3_path, "-p", self.config.pm3_device, "-c", cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self._sim_card = card

            # Give pm3 2 s to connect and start emulating
            await asyncio.sleep(2.0)

            if self._sim_proc.returncode is not None:
                # Exited early — read remaining output
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
        return "PROXMARK" in result["output"].upper() or result["success"]

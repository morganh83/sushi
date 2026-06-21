#!/usr/bin/env python3
"""Sushi — mobile RFID clone controller for Doppelganger Core + Proxmark3."""

import argparse
import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import FileResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from card_commands import get_pm3_command, infer_card_label
from config import Config
from discovery import scan_for_core
from doppelganger import DoppelgangerClient
from proxmark import ProxmarkClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sushi")

config = Config()
doppelganger = DoppelgangerClient(config)
proxmark = ProxmarkClient(config)

ws_clients: set[WebSocket] = set()
seen_ids: set[str] = set()
clone_lock = asyncio.Lock()

STATIC = Path(__file__).parent / "static"


# ── Broadcast ──────────────────────────────────────────────────────────────

async def broadcast(event: dict) -> None:
    dead: set[WebSocket] = set()
    msg = json.dumps(event)
    for ws in list(ws_clients):
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    ws_clients.difference_update(dead)


# ── Clone / emulate ────────────────────────────────────────────────────────

async def do_clone(card: dict, mode: str) -> None:
    label = infer_card_label(card)
    cmd = get_pm3_command(card, mode)

    if not cmd:
        await broadcast({
            "type": "pm3_error",
            "card": card,
            "message": f"No pm3 command for {label} — unsupported type or missing data.",
        })
        return

    await broadcast({"type": "pm3_start", "card": card, "mode": mode,
                     "cmd": cmd, "label": label})

    result = await (proxmark.start_emulation(cmd, card) if mode == "emulate"
                    else proxmark.run_command(cmd))

    await broadcast({
        "type": "pm3_result",
        "card": card, "mode": mode, "label": label,
        "output": result["output"],
        "success": result["success"],
    })


# ── Background poller ──────────────────────────────────────────────────────

async def poll_loop() -> None:
    pm3_open: bool = False
    pm3_check_counter: int = 0

    while True:
        # Check CBP port every 5 poll cycles (~5 s at default interval)
        pm3_check_counter += 1
        if pm3_check_counter >= 5:
            pm3_check_counter = 0
            pm3_open = await proxmark.port_open()

        try:
            cards = await doppelganger.get_cards()
            new_cards = [c for c in cards if c.get("id") and c["id"] not in seen_ids]
            for c in new_cards:
                seen_ids.add(c["id"])

            event: dict = {
                "type": "status",
                "doppelganger": "connected",
                "card_count": len(cards),
                "cards": cards,
                "emulating": proxmark.is_emulating,
                "emulating_card": proxmark.emulating_card,
                "pm3_connected": pm3_open,
            }
            if new_cards:
                event["new_cards"] = new_cards
                log.info("New cards: %s", [c["id"] for c in new_cards])
                if config.auto_clone:
                    async with clone_lock:
                        await do_clone(new_cards[-1], config.clone_mode)

            await broadcast(event)

        except ConnectionError as e:
            await broadcast({
                "type": "status",
                "doppelganger": "disconnected",
                "error": str(e),
                "pm3_connected": pm3_open,
            })
        except Exception as e:
            log.error("Poll error: %s", e)

        await asyncio.sleep(config.poll_interval)


# ── App lifecycle ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: Starlette):
    task = asyncio.create_task(poll_loop())
    log.info("Sushi started — Core: %s  PM3: %s  binary: %s",
             doppelganger.base_url, config.pm3_device, proxmark.binary or "NOT FOUND")
    yield
    task.cancel()
    await doppelganger.close()
    await proxmark.stop_emulation()


# ── Routes ─────────────────────────────────────────────────────────────────

async def root(request) -> FileResponse:
    return FileResponse(STATIC / "index.html")


async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    ws_clients.add(ws)
    try:
        try:
            cards = await doppelganger.get_cards()
        except Exception:
            cards = []

        pm3_open = await proxmark.port_open()
        await ws.send_text(json.dumps({
            "type": "init",
            "config": config.to_dict(),
            "cards": cards,
            "emulating": proxmark.is_emulating,
            "emulating_card": proxmark.emulating_card,
            "pm3_binary": proxmark.binary,
            "pm3_connected": pm3_open,
        }))

        while True:
            data = await ws.receive_json()
            await _handle(data)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.debug("WS session ended: %s", type(e).__name__)
    finally:
        ws_clients.discard(ws)


async def _handle(data: dict) -> None:
    action = data.get("action")

    if action == "clone":
        card = data.get("card")
        mode = data.get("mode") or config.clone_mode
        if card:
            async with clone_lock:
                await do_clone(card, mode)

    elif action == "stop_emulation":
        result = await proxmark.stop_emulation()
        await broadcast({"type": "emulation_stopped", **result})

    elif action == "connect_pm3":
        # Now just a port check — no persistent session needed
        open_ = await proxmark.port_open()
        await broadcast({
            "type": "pm3_connect_result",
            "connected": open_,
            "binary": proxmark.binary,
            "banner": "CBP port reachable" if open_ else "CBP port not reachable — is Communication Bridge Pro running?",
            "error": "" if open_ else "Port not open",
        })

    elif action == "update_config":
        config.update(data.get("data", {}))
        doppelganger.config = config
        await broadcast({"type": "config_updated", "config": config.to_dict()})

    elif action == "clear_seen":
        seen_ids.clear()
        await broadcast({"type": "seen_cleared"})

    elif action == "detect_pm3":
        binary = proxmark.redetect()
        await broadcast({"type": "pm3_detected", "binary": binary, "found": bool(binary)})

    elif action == "ping_proxmark":
        # Full test: run hw version via one-shot command
        result = await proxmark.run_command("hw version", timeout=10.0)
        ok = result["success"] or "PROXMARK" in result["output"].upper()
        await broadcast({"type": "pm3_ping", "connected": ok,
                         "binary": proxmark.binary, "output": result["output"]})

    elif action == "pm3_version":
        version = await proxmark.get_client_version()
        await broadcast({"type": "pm3_version_result", "version": version,
                         "binary": proxmark.binary})

    elif action == "test_core":
        result = await doppelganger.get_raw_csv()
        if result.get("content"):
            try:
                parsed, debug_lines = doppelganger._parse_csv_debug(result["content"])
                result["parsed_count"] = len(parsed)
                result["debug_lines"] = debug_lines
            except Exception as e:
                result["parsed_count"] = -1
                result["debug_lines"] = [f"Parse exception: {e}"]
        await broadcast({"type": "core_raw_csv", **result})

    elif action == "scan_network":
        await broadcast({"type": "scan_started", "target": "core"})
        results = await scan_for_core()
        await broadcast({"type": "scan_result", "target": "core", "devices": results})

    elif action == "install_pm3":
        asyncio.create_task(_run_install())


async def _run_install() -> None:
    """
    Two-phase install:
      Phase 1 — client only (make host, fast, no cross-compiler needed)
      Phase 2 — firmware via proot-distro/Debian (slow, fixes version mismatch)
    """
    home = Path.home()
    prefix = Path(os.environ.get("PREFIX", "/data/data/com.termux/files/usr"))
    clone_dir = home / "proxmark3"

    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}

    async def run_step(label: str, cmd: str, allow_fail: bool = False) -> bool:
        await broadcast({"type": "install_log", "line": f"\n-- {label}", "done": False})
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, env=env,
            )
            async for raw in proc.stdout:
                await broadcast({"type": "install_log",
                                 "line": raw.decode("utf-8", errors="replace").rstrip(),
                                 "done": False})
            await proc.wait()
            if proc.returncode != 0 and not allow_fail:
                await broadcast({"type": "install_log",
                                 "line": f"FAILED (exit {proc.returncode})",
                                 "done": True, "success": False})
                return False
            return True
        except Exception as e:
            await broadcast({"type": "install_log", "line": f"ERROR: {e}",
                             "done": True, "success": False})
            return False

    # ── Phase 1: Build client (host tools only) ───────────────────────────
    await broadcast({"type": "install_log",
                     "line": "=== Phase 1: Building client (host tools) ===", "done": False})

    phase1 = [
        ("Removing pkg version (if any)…",    "pkg uninstall proxmark3 -y", True),
        ("Installing build deps…",             "pkg install -y git clang make cmake python libc++", False),
        ("Cloning iceman fork…",
         f"git clone --depth=1 https://github.com/RfidResearchGroup/proxmark3 {clone_dir}", False),
        # Write platform config — CRITICAL: BTADDON ensures firmware + client match
        ("Writing Makefile.platform (RDV4 + Blueshark)…",
         f"printf 'PLATFORM=PM3RDV4\\nPLATFORM_EXTRAS=BTADDON\\n' > {clone_dir}/Makefile.platform", False),
        # host target = client tools only; no ARM cross-compiler required in Termux
        ("Building client (make host)…",      f"make -C {clone_dir} host -j$(nproc)", False),
        ("Linking proxmark3 and pm3 to PATH…",
         f"ln -sf {clone_dir}/client/proxmark3 {prefix}/bin/proxmark3 && "
         f"ln -sf {clone_dir}/pm3 {prefix}/bin/pm3", False),
    ]

    for label, cmd, allow_fail in phase1:
        if not await run_step(label, cmd, allow_fail):
            return

    binary = proxmark.redetect()
    await broadcast({"type": "install_log",
                     "line": f"\n=== Phase 1 done. Client binary: {binary or 'NOT FOUND'} ===\n",
                     "done": False})

    # ── Phase 2: Build firmware via proot-distro + Debian ────────────────
    await broadcast({"type": "install_log",
                     "line": "=== Phase 2: Building firmware (RDV4 + BTADDON) via proot-distro ===",
                     "done": False})
    await broadcast({"type": "install_log",
                     "line": "    This fixes the 'firmware does not match' error.", "done": False})
    await broadcast({"type": "install_log",
                     "line": "    Takes ~20-30 min. Do NOT close the app.\n", "done": False})

    fw_cmd = (
        f"proot-distro login debian --termux-home -- bash -c '"
        f"apt-get update -qq && "
        f"apt-get install -y gcc-arm-none-eabi libnewlib-dev libnewlib-arm-none-eabi && "
        f"make -C {clone_dir} -j$(nproc) fullimage"
        f"'"
    )

    phase2 = [
        ("Installing proot-distro…",                  "pkg install -y proot-distro", False),
        ("Installing Debian (if not already done)…",  "proot-distro install debian", True),
        ("Building firmware in Debian (long step)…",  fw_cmd, False),
    ]

    for label, cmd, allow_fail in phase2:
        if not await run_step(label, cmd, allow_fail):
            return

    fullimage = clone_dir / "fullimage.elf"
    fw_found = fullimage.exists()

    await broadcast({"type": "install_log",
                     "line": f"\n=== Phase 2 done. Firmware: {fullimage} {'✓' if fw_found else 'NOT FOUND'} ===",
                     "done": False})

    if fw_found:
        await broadcast({"type": "install_log", "line": "\nTo flash firmware:", "done": False})
        await broadcast({"type": "install_log",
                         "line": "  1. Hold side button on Proxmark3 while connecting via USB OTG",
                         "done": False})
        await broadcast({"type": "install_log",
                         "line": f"  2. pm3 -p tcp:localhost:4321 --flash --image {fullimage}",
                         "done": False})
        await broadcast({"type": "install_log",
                         "line": "  WARNING: Only flash fullimage, NOT bootrom, from Android.",
                         "done": False})

    await broadcast({"type": "install_log", "line": "\n=== All done ===",
                     "done": True, "success": fw_found})
    await broadcast({"type": "pm3_detected", "binary": binary, "found": bool(binary)})


app = Starlette(
    routes=[
        Route("/", root),
        WebSocketRoute("/ws", ws_endpoint),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ],
    lifespan=lifespan,
)


# ── Entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="Sushi — RFID clone controller")
    parser.add_argument("--port", type=int, default=config.server_port)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    print(f"\n  SUSHI")
    print(f"  Core   : http://{config.doppelganger_ip}:{config.doppelganger_port}")
    print(f"  PM3    : {config.pm3_device}")
    print(f"  Browser: http://localhost:{args.port}\n")

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")

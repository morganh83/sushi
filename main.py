#!/usr/bin/env python3
"""Sushi — mobile RFID clone controller for Doppelganger Core + Proxmark3."""

import argparse
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import FileResponse, HTMLResponse
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
    ws_clients -= dead


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
    while True:
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
            })
        except Exception as e:
            log.error("Poll error: %s", e)

        await asyncio.sleep(config.poll_interval)


# ── App lifecycle ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: Starlette):
    task = asyncio.create_task(poll_loop())
    log.info("Sushi started — Core: %s  PM3: %s", doppelganger.base_url, config.pm3_device)
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
        cards = await doppelganger.get_cards()
    except Exception:
        cards = []

    await ws.send_text(json.dumps({
        "type": "init",
        "config": config.to_dict(),
        "cards": cards,
        "emulating": proxmark.is_emulating,
        "emulating_card": proxmark.emulating_card,
    }))

    try:
        while True:
            data = await ws.receive_json()
            await _handle(data)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.error("WS error: %s", e)
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

    elif action == "update_config":
        config.update(data.get("data", {}))
        doppelganger.config = config
        await broadcast({"type": "config_updated", "config": config.to_dict()})

    elif action == "clear_seen":
        seen_ids.clear()
        await broadcast({"type": "seen_cleared"})

    elif action == "ping_proxmark":
        ok = await proxmark.ping()
        await broadcast({"type": "pm3_ping", "connected": ok})

    elif action == "scan_network":
        await broadcast({"type": "scan_started", "target": "core"})
        results = await scan_for_core()
        await broadcast({"type": "scan_result", "target": "core", "devices": results})


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
    print(f"  BT MAC : {config.bt_address or '(not set)'}")
    print(f"  Browser: http://localhost:{args.port}\n")

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")

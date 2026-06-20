"""
Bluetooth SPP → TCP bridge.

Connects to the Proxmark3 Blueshark module via RFCOMM (Bluetooth Classic,
channel 1 = standard SPP) and exposes it as a local TCP server so pm3 can
connect with  -p tcp://localhost:<port>.

Replaces Communication Bridge Pro.
"""

import asyncio
import logging
import socket
from typing import Optional

log = logging.getLogger("sushi.bt")

RFCOMM_CHANNEL = 1  # SPP standard; Blueshark always uses channel 1


class BluetoothBridge:
    def __init__(self, config) -> None:
        self.config = config
        self._bt_sock: Optional[socket.socket] = None
        self._server: Optional[asyncio.Server] = None
        self._active_writers: list[asyncio.StreamWriter] = []

    # ── Public state ──────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._bt_sock is not None and self._server is not None

    @property
    def bt_address(self) -> str:
        return self.config.bt_address

    @property
    def tcp_port(self) -> int:
        return int(self.config.bt_port)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> dict:
        """Connect to BT device and start TCP bridge. Returns {success, error?}."""
        await self.stop()

        addr = self.bt_address
        if not addr:
            return {"success": False, "error": "No Bluetooth address configured."}

        loop = asyncio.get_event_loop()

        # Open RFCOMM socket
        try:
            sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            sock.setblocking(False)
            await loop.sock_connect(sock, (addr, RFCOMM_CHANNEL))
            self._bt_sock = sock
            log.info("BT connected: %s", addr)
        except OSError as e:
            return {"success": False, "error": f"BT connect failed: {e}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

        # Start TCP listener
        try:
            self._server = await asyncio.start_server(
                self._handle_tcp_client, "127.0.0.1", self.tcp_port,
            )
            log.info("BT bridge listening on tcp://localhost:%d", self.tcp_port)
            return {"success": True, "port": self.tcp_port, "address": addr}
        except Exception as e:
            await self.stop()
            return {"success": False, "error": f"TCP server failed: {e}"}

    async def stop(self) -> None:
        """Disconnect BT and shut down TCP bridge."""
        for writer in list(self._active_writers):
            try:
                writer.close()
            except Exception:
                pass
        self._active_writers.clear()

        if self._server:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None

        if self._bt_sock:
            try:
                self._bt_sock.close()
            except Exception:
                pass
            self._bt_sock = None

        log.info("BT bridge stopped")

    # ── Internal: bridge one TCP client ↔ RFCOMM ─────────────────────────

    async def _handle_tcp_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._active_writers.append(writer)
        loop = asyncio.get_event_loop()
        bt = self._bt_sock
        log.info("pm3 connected via bridge")

        async def tcp_to_bt() -> None:
            try:
                while True:
                    data = await reader.read(4096)
                    if not data or bt is None:
                        break
                    await loop.sock_sendall(bt, data)
            except Exception:
                pass

        async def bt_to_tcp() -> None:
            try:
                while True:
                    if bt is None:
                        break
                    data = await loop.sock_recv(bt, 4096)
                    if not data:
                        break
                    writer.write(data)
                    await writer.drain()
            except Exception:
                pass

        await asyncio.gather(tcp_to_bt(), bt_to_tcp())

        try:
            writer.close()
        except Exception:
            pass
        if writer in self._active_writers:
            self._active_writers.remove(writer)
        log.info("pm3 disconnected from bridge")

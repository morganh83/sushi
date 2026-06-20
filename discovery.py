"""Network and Bluetooth device discovery."""

import asyncio
import socket
import subprocess
from typing import Optional

import httpx


def get_local_subnets() -> list[str]:
    """Return /24 subnet prefixes reachable from this host, excluding loopback."""
    subnets: list[str] = []
    seen: set[str] = set()

    # Try routing-trick for common hotspot gateways first
    for gateway in ("192.168.43.1", "192.168.49.1", "10.0.0.1", "192.168.1.1"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(0.05)
                s.connect((gateway, 80))
                ip = s.getsockname()[0]
                if not ip.startswith("127.") and ip not in seen:
                    seen.add(ip)
                    subnets.append(ip.rsplit(".", 1)[0])
        except Exception:
            pass

    # Fallback: hostname resolution
    if not subnets:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                prefix = ip.rsplit(".", 1)[0]
                if not ip.startswith("127.") and ip not in seen:
                    seen.add(ip)
                    subnets.append(prefix)
        except Exception:
            pass

    return subnets


async def scan_for_core(timeout: float = 1.5) -> list[dict]:
    """
    Scan all reachable /24 subnets for a Doppelganger Core (responds to
    /reader_config.json with HTTP 200).  Returns list of {ip, reader_type}.
    """
    subnets = get_local_subnets()
    if not subnets:
        return []

    found: list[dict] = []

    async def probe(ip: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.get(f"http://{ip}/reader_config.json")
                if r.status_code == 200:
                    try:
                        data = r.json()
                        reader_type = str(data.get("READER_TYPE", "HID")).upper()
                    except Exception:
                        reader_type = "HID"
                    found.append({"ip": ip, "reader_type": reader_type})
        except Exception:
            pass

    tasks = []
    for subnet in subnets:
        for i in range(1, 255):
            tasks.append(probe(f"{subnet}.{i}"))

    await asyncio.gather(*tasks)
    return found


def list_paired_bt_devices() -> list[dict]:
    """
    Return paired Bluetooth devices via bluetoothctl.
    Returns list of {address, name}.  Empty list if bluetoothctl unavailable.
    """
    try:
        result = subprocess.run(
            ["bluetoothctl", "paired-devices"],
            capture_output=True, text=True, timeout=5,
        )
        devices = []
        for line in result.stdout.splitlines():
            # Line format: "Device AA:BB:CC:DD:EE:FF Some Name"
            parts = line.strip().split(" ", 2)
            if len(parts) == 3 and parts[0] == "Device":
                devices.append({"address": parts[1], "name": parts[2]})
        return devices
    except FileNotFoundError:
        return []
    except Exception:
        return []

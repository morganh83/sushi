import json
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "sushi_config.json"

DEFAULTS: dict = {
    "doppelganger_ip": "192.168.4.1",
    "doppelganger_port": 80,
    "pm3_device": "tcp://localhost:4321",
    "pm3_path": "pm3",
    "auto_clone": False,
    "clone_mode": "emulate",       # "write" or "emulate"
    "poll_interval": 1.0,
    "reader_mode": "hid",          # "hid" or "paxton"
    "server_port": 8080,
    "bt_address": "",              # Proxmark3 Blueshark BT MAC address
    "bt_port": 4321,               # Local TCP port the BT bridge listens on
}

ALLOWED_UPDATES = {
    "auto_clone", "clone_mode", "doppelganger_ip", "doppelganger_port",
    "pm3_device", "pm3_path", "poll_interval", "reader_mode",
    "bt_address", "bt_port",
}


class Config:
    def __init__(self) -> None:
        self._data: dict = dict(DEFAULTS)
        self._load()

    def _load(self) -> None:
        if CONFIG_FILE.exists():
            try:
                saved = json.loads(CONFIG_FILE.read_text())
                self._data.update({k: v for k, v in saved.items() if k in self._data})
            except Exception:
                pass

    def save(self) -> None:
        CONFIG_FILE.write_text(json.dumps(self._data, indent=2))

    def update(self, data: dict) -> None:
        filtered = {k: v for k, v in data.items() if k in ALLOWED_UPDATES}
        self._data.update(filtered)
        self.save()

    def to_dict(self) -> dict:
        return dict(self._data)

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(f"Config has no attribute '{name}'")

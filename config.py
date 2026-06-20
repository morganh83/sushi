import json
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "sushi_config.json"

DEFAULTS: dict = {
    "doppelganger_ip": "192.168.4.1",
    "doppelganger_port": 80,
    "pm3_device": "tcp:localhost:4321",   # CBP default port, no double-slash
    "pm3_device_type": "rdv4_bt",         # rdv4_bt | rdv4 | rdv3 | generic
    "auto_clone": False,
    "clone_mode": "emulate",
    "poll_interval": 1.0,
    "reader_mode": "hid",
    "server_port": 8080,
}

ALLOWED_UPDATES = {
    "auto_clone", "clone_mode", "doppelganger_ip", "doppelganger_port",
    "pm3_device", "pm3_device_type", "poll_interval", "reader_mode",
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
            self.save()  # rewrite without any keys removed from DEFAULTS

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

"""Local card cache — persists captures so they survive Core power-off."""

import json
import logging
import time
from pathlib import Path

STORE_FILE = Path(__file__).parent / "sushi_cards.json"
log = logging.getLogger("sushi.store")


def load() -> list[dict]:
    if not STORE_FILE.exists():
        return []
    try:
        data = json.loads(STORE_FILE.read_text())
        return data.get("cards", [])
    except Exception as e:
        log.error("card store load failed: %s", e)
        return []


def save(cards: list[dict]) -> None:
    try:
        STORE_FILE.write_text(json.dumps(
            {"cards": cards, "saved": time.time()}, indent=2
        ))
    except Exception as e:
        log.error("card store save failed: %s", e)


def sync_from_core(core_cards: list[dict]) -> list[dict]:
    """
    Merge Core cards into the local store. Core is master for fields it provides.
    Returns the updated full list (Core cards + any locally-only stored cards).
    """
    local = load()
    by_id: dict[str, dict] = {c["id"]: dict(c) for c in local if c.get("id")}

    for card in core_cards:
        cid = card.get("id", "")
        if not cid:
            continue
        if cid in by_id:
            # Core is authoritative — update fields but keep any local extras
            by_id[cid].update(card)
        else:
            by_id[cid] = dict(card)

    merged = list(by_id.values())
    save(merged)
    return merged

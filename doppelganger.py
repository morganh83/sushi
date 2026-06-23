import csv
import io
from typing import Optional

import httpx


class DoppelgangerClient:
    def __init__(self, config) -> None:
        self.config = config
        self._http: Optional[httpx.AsyncClient] = None

    @property
    def base_url(self) -> str:
        return f"http://{self.config.doppelganger_ip}:{self.config.doppelganger_port}"

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=5.0)
        return self._http

    async def ping(self) -> bool:
        try:
            c = await self._client()
            r = await c.get(self.base_url, timeout=3.0)
            return r.status_code < 500
        except Exception:
            return False

    async def get_reader_mode(self) -> str:
        """Return 'paxton' or 'hid' by checking reader_config.json."""
        try:
            c = await self._client()
            r = await c.get(f"{self.base_url}/reader_config.json", timeout=3.0)
            data = r.json()
            rt = str(data.get("READER_TYPE", "HID")).upper()
            return "paxton" if "PAXTON" in rt else "hid"
        except Exception:
            return self.config.reader_mode

    async def get_cards(self) -> list[dict]:
        try:
            c = await self._client()
            r = await c.get(f"{self.base_url}/cards.csv", timeout=5.0)
            r.raise_for_status()
            return self._parse_csv(r.text)
        except httpx.HTTPStatusError as e:
            raise ConnectionError(f"Doppelganger HTTP error: {e.response.status_code}")
        except Exception as e:
            raise ConnectionError(f"Doppelganger unreachable: {e}")

    async def get_raw_csv(self) -> dict:
        """Fetch cards.csv and return raw content for diagnostics."""
        url = f"{self.base_url}/cards.csv"
        try:
            c = await self._client()
            r = await c.get(url, timeout=5.0)
            return {
                "url": url,
                "status": r.status_code,
                "content": r.text[:3000],
                "length": len(r.text),
                "error": None,
            }
        except Exception as e:
            return {"url": url, "status": None, "content": "", "length": 0, "error": str(e)}

    def _parse_csv_debug(self, content: str) -> tuple[list[dict], list[str]]:
        """Parse CSV and return (cards, debug_lines) showing what happened to each row."""
        log: list[str] = []
        content = content.strip()
        if not content:
            log.append("CSV content is empty")
            return [], log

        reader = csv.reader(io.StringIO(content))
        rows = [r for r in reader if any(c.strip() for c in r)]
        log.append(f"Total non-empty rows: {len(rows)}")
        if not rows:
            return [], log

        first = [c.strip().upper() for c in rows[0]]
        has_header = any(col in first for col in ("BL", "FC", "CN", "TYPE", "TOKEN", "HEX"))
        log.append(f"Row 0 (raw): {rows[0]}")
        log.append(f"Row 0 (upper): {first}")
        log.append(f"Header detected: {has_header}")

        header = first if has_header else None
        data_rows = rows[1:] if has_header else rows
        log.append(f"Data rows to parse: {len(data_rows)}")

        cards = []
        for i, raw_row in enumerate(data_rows):
            row = [c.strip() for c in raw_row]
            card = self._parse_row(row, header)
            if card:
                log.append(f"Row {i}: OK → {card.get('id')}")
                cards.append(card)
            else:
                log.append(f"Row {i}: SKIPPED → {row[:5]}")

        return cards, log

    def _parse_csv(self, content: str) -> list[dict]:
        content = content.strip()
        if not content:
            return []

        reader = csv.reader(io.StringIO(content))
        rows = [r for r in reader if any(c.strip() for c in r)]
        if not rows:
            return []

        first = [c.strip().upper() for c in rows[0]]
        has_header = any(col in first for col in ("BL", "FC", "CN", "TYPE", "TOKEN", "HEX"))
        header = first if has_header else None
        data_rows = rows[1:] if has_header else rows

        # Deduplicate by ID but track how many times each card was scanned.
        # scan_count > 1 means the Core picked up the same credentials more
        # than once — useful for confirming a successful clone.
        by_id: dict[str, dict] = {}
        for row in data_rows:
            card = self._parse_row([c.strip() for c in row], header)
            if card:
                cid = card.get("id", "")
                if cid in by_id:
                    by_id[cid]["scan_count"] = by_id[cid].get("scan_count", 1) + 1
                else:
                    card["scan_count"] = 1
                    by_id[cid] = card
        return list(by_id.values())

    def _parse_row(self, row: list[str], header: Optional[list[str]]) -> Optional[dict]:
        if not row:
            return None

        # ── Key:Value format ──────────────────────────────────────────────
        # The Doppelganger Core writes each row as self-describing key:value
        # pairs, e.g.:
        #   DATA_TYPE: CARD, Format: C1k35s (C-1000), Bit_Length: 35,
        #   Hex_Value: 2D4A64FB8E, Facility_Code: 2643, Card_Number: 163271, BIN: ...
        if ": " in row[0]:
            kv: dict[str, str] = {}
            for col in row:
                col = col.strip()
                if ": " in col:
                    key, _, val = col.partition(": ")
                    # Normalise: upper-case, underscores instead of spaces
                    kv[key.strip().upper().replace(" ", "_")] = val.strip()

            data_type = kv.get("DATA_TYPE", "CARD").upper()

            # ── Paxton / Net2 ─────────────────────────────────────────────
            if "NET2" in data_type or "PAXTON" in data_type:
                token = kv.get("TOKEN", kv.get("CARD_NUMBER", ""))
                return {
                    "card_type": "paxton",
                    "type_str": kv.get("FORMAT", "Net2"),
                    "token": token,
                    "hex": kv.get("HEX_VALUE", kv.get("HEX", "")),
                    "bl": "", "fc": token, "cn": "",
                    "id": f"paxton-{token}",
                }

            # ── HID / Wiegand ─────────────────────────────────────────────
            if "CARD" in data_type or not data_type:
                bl   = kv.get("BIT_LENGTH", "")
                fc   = kv.get("FACILITY_CODE", "")
                cn   = kv.get("CARD_NUMBER", "")
                hex_ = kv.get("HEX_VALUE", kv.get("HEX", ""))
                bin_ = kv.get("BIN", "")
                fmt  = kv.get("FORMAT", "")

                if not any([bl, fc, cn]):
                    return None
                try:
                    int(bl)
                except (ValueError, TypeError):
                    return None

                return {
                    "card_type": "hid",
                    "bl": bl, "fc": fc, "cn": cn,
                    "hex": hex_, "bin": bin_,
                    "format": fmt,
                    "id": f"hid-{bl}-{fc}-{cn}",
                }
            return None

        # ── Standard column-based CSV (header or positional) ─────────────
        rd = dict(zip(header, row)) if header else {}

        # Paxton column format
        if "TYPE" in rd or "TOKEN" in rd:
            token = rd.get("TOKEN", row[1] if len(row) > 1 else "")
            return {
                "card_type": "paxton",
                "type_str": rd.get("TYPE", row[0] if row else ""),
                "token": token,
                "hex": rd.get("HEX", row[2] if len(row) > 2 else ""),
                "bl": "", "fc": token, "cn": "",
                "id": f"paxton-{token}",
            }

        # HID column format
        bl  = rd.get("BL",  row[0] if len(row) > 0 else "")
        fc  = rd.get("FC",  row[1] if len(row) > 1 else "")
        cn  = rd.get("CN",  row[2] if len(row) > 2 else "")
        hex_ = rd.get("HEX", rd.get("CSVHEX", row[3] if len(row) > 3 else ""))
        bin_ = rd.get("BIN", rd.get("DATASTREAMBIN", row[4] if len(row) > 4 else ""))

        if not any([bl, fc, cn]):
            return None
        try:
            int(bl)
        except (ValueError, TypeError):
            return None

        return {
            "card_type": "hid",
            "bl": bl, "fc": fc, "cn": cn,
            "hex": hex_, "bin": bin_,
            "id": f"hid-{bl}-{fc}-{cn}",
        }

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

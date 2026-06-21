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

        cards = []
        for row in data_rows:
            card = self._parse_row([c.strip() for c in row], header)
            if card:
                cards.append(card)
        return cards

    def _parse_row(self, row: list[str], header: Optional[list[str]]) -> Optional[dict]:
        if not row:
            return None

        rd = dict(zip(header, row)) if header else {}

        # ── Paxton / Net2 mode ────────────────────────────────────────────
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

        # ── HID / Wiegand mode ────────────────────────────────────────────
        bl  = rd.get("BL",  row[0] if len(row) > 0 else "")
        fc  = rd.get("FC",  row[1] if len(row) > 1 else "")
        cn  = rd.get("CN",  row[2] if len(row) > 2 else "")
        hex_ = rd.get("HEX", rd.get("CSVHEX", row[3] if len(row) > 3 else ""))
        bin_ = rd.get("BIN", rd.get("DATASTREAMBIN", row[4] if len(row) > 4 else ""))

        # Skip entirely empty rows
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

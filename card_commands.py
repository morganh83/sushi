# Maps Doppelganger card data → pm3 command strings.
# All commands are taken from the Doppelganger Assistant source (card_writer.go / card_simulators.go).

HID_FORMAT_MAP: dict[int, str] = {
    26: "H10301",
    27: "H10307",
    28: "2804W",
    30: "ATSW30",
    31: "ADT31",
    33: "D10202",
    34: "H10306",
    35: "C1k35s",
    36: "S12906",
    37: "H10304",
    46: "H800002",
    48: "C1k48s",
    56: "Avig56",
}

INDALA_FORMAT_MAP: dict[int, str] = {
    26: "ind26",
    27: "ind27",
    29: "ind29",
}


def get_pm3_command(card: dict, mode: str) -> str | None:
    """Return the pm3 command string for card + mode, or None if unsupported."""
    card_type = str(card.get("card_type", "hid")).lower()
    mode = mode.lower()

    try:
        bl = int(card.get("bl") or 0)
        fc = int(card.get("fc") or 0)
        cn = int(card.get("cn") or 0)
    except (ValueError, TypeError):
        bl = fc = cn = 0

    hex_data = str(card.get("hex") or "").replace(" ", "").replace("0x", "").upper()

    # ── EM4100 ────────────────────────────────────────────────────────────
    if card_type == "em4100":
        return (f"lf em 410x clone --id {hex_data}" if mode == "write"
                else f"lf em 410x sim --id {hex_data}")

    # ── AWID ──────────────────────────────────────────────────────────────
    if card_type == "awid":
        return (f"lf awid clone --fmt 26 --fc {fc} --cn {cn}" if mode == "write"
                else f"lf awid sim --fmt 26 --fc {fc} --cn {cn}")

    # ── Indala ────────────────────────────────────────────────────────────
    if card_type == "indala":
        ind_fmt = INDALA_FORMAT_MAP.get(bl, "ind26")
        return (f"lf indala clone --fc {fc} --cn {cn}" if mode == "write"
                else f"lf indala sim --fc {fc} --cn {cn}")

    # ── Avigilon (HID 56-bit) ─────────────────────────────────────────────
    if card_type == "avigilon":
        return (f"lf hid clone -w Avig56 --fc {fc} --cn {cn}" if mode == "write"
                else f"lf hid sim -w Avig56 --fc {fc} --cn {cn}")

    # ── MIFARE ────────────────────────────────────────────────────────────
    if card_type == "mifare":
        uid = hex_data[:8]
        return (f"hf mf csetuid -u {uid}" if mode == "write"
                else f"hf 14a sim -t 1 --uid {uid}")

    # ── PIV ───────────────────────────────────────────────────────────────
    if card_type == "piv":
        uid = hex_data[:16]
        return f"hf 14a sim -t 3 --uid {uid}"

    # ── iCLASS ───────────────────────────────────────────────────────────
    if card_type == "iclass":
        if mode == "write":
            # -w (Wiegand format) is required — use the format field from the
            # card dict if available, otherwise infer from bit length.
            # Without -w the command uses no Wiegand encoding and will write
            # wrong data.
            fmt = str(card.get("format") or "").split()[0]  # e.g. "C1k35s"
            if not fmt:
                fmt = HID_FORMAT_MAP.get(bl, "H10301")
            return f"hf iclass encode --ki 0 --fc {fc} --cn {cn} -w {fmt}"
        return None  # iCLASS sim requires a pre-dumped file

    # ── Paxton Net2 ───────────────────────────────────────────────────────
    if card_type == "paxton":
        token = str(card.get("token") or "")
        if mode == "write":
            return f"hf iclass encode --paxton {token}" if token else None
        return None  # Paxton sim not straightforward via CLI

    # ── HID / Generic Wiegand (default) ──────────────────────────────────
    # 32-bit on a HID reader typically means EM4100
    if bl == 32 and hex_data:
        return (f"lf em 410x clone --id {hex_data}" if mode == "write"
                else f"lf em 410x sim --id {hex_data}")

    hid_fmt = HID_FORMAT_MAP.get(bl)
    if hid_fmt is None:
        return None

    return (f"lf hid clone -w {hid_fmt} --fc {fc} --cn {cn}" if mode == "write"
            else f"lf hid sim -w {hid_fmt} --fc {fc} --cn {cn}")


def infer_card_label(card: dict) -> str:
    """Human-readable card type label."""
    card_type = str(card.get("card_type", "hid")).lower()
    bl = card.get("bl", 0)

    labels = {
        "paxton": "Paxton Net2",
        "em4100": "EM4100",
        "mifare": "MIFARE",
        "iclass": "iCLASS",
        "awid": "AWID",
        "indala": "Indala",
        "piv": "PIV/SEOS",
        "avigilon": "Avigilon",
    }
    if card_type in labels:
        return labels[card_type]

    try:
        bl_int = int(bl)
    except (ValueError, TypeError):
        return f"HID {bl}-bit" if bl else "Unknown"

    if bl_int == 32:
        return "EM4100 (32-bit)"

    fmt = HID_FORMAT_MAP.get(bl_int)
    return f"HID {fmt} ({bl_int}-bit)" if fmt else f"HID {bl_int}-bit"

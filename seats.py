#!/usr/bin/env python3
"""Zjišťuje, která konkrétní sedadla jsou u představení ještě volná.

Rozpis z data-api-service (viz watch.py) umí říct jen `availabilityRatio`,
tedy kolik procent sálu je volných — ne kde ta místa jsou. To nestačí:
u vyprodávaných projekcí zbývají poslední místa v prvních dvou řadách,
což je na 70mm IMAXu k ničemu.

Skutečný plán sálu má rezervační systém na tickets.cinemacity.cz. Používají
se dva jeho endpointy (oba GET, bez přihlášení, bez cookies):

  /api/presentations/{id}            → seatplanId + venueId daného sálu
  /api/seats/seatplan?seatplanId=&venueId=  → rozložení sálu (řady, sedadla)
  /api/seats/seats-statusV2?presentationId= → klíče právě VOLNÝCH sedadel

Jediná podmínka: hlavička `uuid` musí obsahovat platné UUID verze 4. Bez ní
i s neplatným tvarem vrací API 403. Nemusí se nikde registrovat, stačí
pokaždé vygenerovat nové.

Klíč sedadla má tvar `{sekce}_{X}_{Y}`, kde Y je pořadí řady od plátna
a X pozice v řadě. Sousední sedadla mají sousední X — žádná řada v IMAX
VOLVO nemá v X díru, takže rozestup 1 opravdu znamená "vedle sebe".
"""

import json
import os
import time
import urllib.error
import urllib.request
import uuid

TICKETS = "https://tickets.cinemacity.cz"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Od které řady (počítáno od plátna) má smysl na 70mm sedět.
MIN_ROW = int(os.environ.get("MIN_ROW", "5"))
# Kolik sedadel vedle sebe se v hlášení zvýrazňuje jako "sedíme spolu".
PREFERRED_BLOCK = int(os.environ.get("PREFERRED_BLOCK", "3"))
DELAY = float(os.environ.get("REQUEST_DELAY", "0.25"))


def api(path, referer):
    url = f"{TICKETS}{path}"
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "cs-CZ,cs;q=0.9",
                    "Referer": referer,
                    # Bez platného UUIDv4 vrací API 403.
                    "uuid": str(uuid.uuid4()),
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"tickets API selhalo po 4 pokusech: {url}\n{last}")


def fetch_layout(presentation_id):
    """Vrátí {klíč sedadla: (číslo řady, název sedadla, X)} pro celý sál.

    Plán je vlastnost sálu, ne představení, takže se dá načíst jednou
    a použít pro všechny projekce ve stejném sále.
    """
    referer = f"{TICKETS}/order/{presentation_id}"
    time.sleep(DELAY)
    pres = api(f"/api/presentations/{presentation_id}", referer)["presentation"]
    time.sleep(DELAY)
    plan = api(
        f"/api/seats/seatplan?seatplanId={pres['seatplanId']}&venueId={pres['venueId']}",
        referer,
    )

    layout = {}
    for section_id, section in plan.get("sections", {}).items():
        for group in section.get("groups", {}).values():
            for row in group.get("rows", {}).values():
                row_name = row.get("RowName")
                if not str(row_name).isdigit():
                    continue
                for seat in row.get("seats", {}).values():
                    key = f"{section_id}_{seat['XCoordinate']}_{seat['YCoordinate']}"
                    layout[key] = (int(row_name), seat.get("SeatName"), seat["XCoordinate"])
    return layout


def fetch_free(presentation_id):
    """Klíče sedadel, která jsou právě volná (API vrací jen ta)."""
    body = api(
        f"/api/seats/seats-statusV2?presentationId={presentation_id}",
        f"{TICKETS}/order/{presentation_id}",
    )
    return set(body.get("seats", {}))


def is_regular(seat_name):
    """Místa pro vozíčkáře se jmenují V1–V6, běžná sedadla mají jen číslo."""
    return str(seat_name).isdigit()


def good_seats(free_keys, layout, min_row=None):
    """Volná běžná sedadla od `min_row` dál, poskládaná do souvislých skupin.

    Vrací seznam řad: [{"row": 8, "groups": [["12","13","14"], ["30"]]}, …]
    kde každá skupina je sada sedadel vedle sebe. Skupiny se nesmí slévat —
    "řada 1: 1–23" by u volných sedadel 1–8 a 14–23 vypadalo jako 23 míst
    v kuse, i když jsou to dvě party a mezi nimi obsazený střed.
    """
    min_row = MIN_ROW if min_row is None else min_row
    by_row = {}
    for key in free_keys:
        info = layout.get(key)
        if not info:
            continue
        row, seat_name, x = info
        if row < min_row or not is_regular(seat_name):
            continue
        by_row.setdefault(row, []).append((x, seat_name))

    out = []
    for row in sorted(by_row):
        ordered = sorted(by_row[row])
        groups, run = [], [ordered[0][1]]
        for (prev_x, _), (x, name) in zip(ordered, ordered[1:]):
            if x - prev_x == 1:
                run.append(name)
            else:
                groups.append(run)
                run = [name]
        groups.append(run)
        out.append({"row": row, "groups": [sorted(g, key=int) for g in groups]})
    return out


def seat_ids(rows):
    """Stabilní identifikátory sedadel pro porovnání mezi běhy."""
    return sorted(f"{r['row']}-{s}" for r in rows for g in r["groups"] for s in g)


def _span(group):
    return group[0] if len(group) == 1 else f"{group[0]}–{group[-1]}"


def describe(rows):
    """Lidsky čitelný popis: 'řada 8: 12–14 (3 vedle sebe), řada 11: 7'."""
    parts = []
    for r in rows:
        chunks = []
        for g in sorted(r["groups"], key=len, reverse=True):
            note = f" ({len(g)} vedle sebe)" if len(g) >= PREFERRED_BLOCK else ""
            chunks.append(f"{_span(g)}{note}")
        parts.append(f"řada {r['row']}: " + ", ".join(chunks))
    return " · ".join(parts)


def best_block(rows):
    return max((len(g) for r in rows for g in r["groups"]), default=0)


def total_seats(rows):
    return sum(len(g) for r in rows for g in r["groups"])

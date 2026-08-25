#!/usr/bin/env python3
"""Jednorázový výpis: volná místa od řady 5, projekce od 16:00, min. 3 místa (nemusí být vedle sebe)."""
import time, watch, seats

MIN_ROW = 5
MIN_SEATS = 3
FROM_HOUR = 16

events = watch.collect()
upcoming = sorted((e for e in events.values() if e["datetime"] >= watch.now().isoformat()),
                  key=lambda x: x["datetime"])
print(f"nalezeno {len(events)} projekcí, z toho {len(upcoming)} budoucích\n")

layouts = {}
for e in upcoming:
    dt = e["datetime"]
    hour = int(dt[11:13])
    if hour < FROM_HOUR:
        print(f"{dt}  {e['cinema']:<28} {e['auditorium']:<12} — před {FROM_HOUR}:00, přeskočeno")
        continue
    hall = (e["cinemaId"], e["auditorium"])
    try:
        if hall not in layouts:
            layouts[hall] = seats.fetch_layout(e["pres"])
        time.sleep(0.25)
        rows = seats.good_seats(seats.fetch_free(e["pres"]), layouts[hall], min_row=MIN_ROW)
    except Exception as exc:
        print(f"{dt}  {e['cinema']} — CHYBA: {exc}")
        continue
    total = seats.total_seats(rows)
    block = seats.best_block(rows)
    mark = "OK " if total >= MIN_SEATS else "   "
    print(f"{mark}{dt}  {e['cinema']:<28} {e['auditorium']:<12} volných(>={MIN_ROW}. řada)={total:<4} největší blok={block}")
    if total >= MIN_SEATS:
        print(f"     {seats.describe(rows)}")
        print(f"     {e['booking']}")

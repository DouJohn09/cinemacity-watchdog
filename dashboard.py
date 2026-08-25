#!/usr/bin/env python3
"""Vygeneruje přehled volných míst na Odysseu v 70mm IMAXu — data i HTML.

Doplněk k hlídání (watch.py): to hlásí jen *změny* e-mailem, tady je vidět
celkový stav všech vypsaných projekcí naráz.

Kritéria "dobrého místa" se dají přenastavit přes prostředí:
  MIN_ROW=5      od které řady (od plátna) se místo počítá
  MIN_SEATS=3    kolik volných míst v dobrých řadách dělá "hratelný" termín
  FROM_HOUR=16   projekce dřív než tahle hodina se jen zobrazí, nezapočítají

Píše dashboard.json (data) a dashboard.html (stránka k publikování).
"""

import collections
import html
import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import seats
import watch

MIN_ROW = int(os.environ.get("MIN_ROW", "5"))
MIN_SEATS = int(os.environ.get("MIN_SEATS", "3"))
FROM_HOUR = int(os.environ.get("FROM_HOUR", "16"))
OUT_JSON = os.environ.get("OUT_JSON", "dashboard.json")
OUT_HTML = os.environ.get("OUT_HTML", "dashboard.html")

CZ_DAYS = ["pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle"]
CZ_DAYS_SHORT = ["po", "út", "st", "čt", "pá", "so", "ne"]
CZ_MONTHS = ["", "ledna", "února", "března", "dubna", "května", "června",
             "července", "srpna", "září", "října", "listopadu", "prosince"]


def collect_data():
    events = watch.collect()
    now = watch.now()
    upcoming = sorted(
        (e for e in events.values() if e["datetime"] >= now.isoformat()),
        key=lambda x: x["datetime"],
    )

    layouts = {}
    out = []
    for e in upcoming:
        hall = (e["cinemaId"], e["auditorium"])
        try:
            if hall not in layouts:
                layouts[hall] = seats.fetch_layout(e["pres"])
            layout = layouts[hall]
            time.sleep(seats.DELAY)
            free = seats.fetch_free(e["pres"])
        except (RuntimeError, KeyError, TypeError) as exc:
            print(f"  ! {e['datetime']}: stav míst se nepodařilo zjistit: {exc}")
            continue

        per_row = collections.Counter()
        accessible = 0
        for key in free:
            info = layout.get(key)
            if not info:
                continue
            row, seat_name, _ = info
            if seats.is_regular(seat_name):
                per_row[row] += 1
            else:
                accessible += 1

        rows = seats.good_seats(free, layout, min_row=MIN_ROW)
        dt = datetime.fromisoformat(e["datetime"])
        good = seats.total_seats(rows)
        late = dt.hour >= FROM_HOUR
        out.append({
            "datetime": e["datetime"],
            "date": e["datetime"][:10],
            "time": f"{dt:%H:%M}",
            "weekday": dt.weekday(),
            "cinema": e["cinema"],
            "auditorium": e["auditorium"],
            "booking": e["booking"],
            "attrs": e["attrs"],
            "hall_size": len(layout),
            "free_regular": sum(per_row.values()),
            "free_accessible": accessible,
            "per_row": {str(r): per_row.get(r, 0) for r in range(1, max(per_row or [12]) + 1)},
            "max_row": max((r for _, (r, _, _) in layout.items()), default=12),
            "good_seats": good,
            "best_block": seats.best_block(rows),
            "detail": seats.describe(rows) if rows else "",
            "late": late,
            "match": late and good >= MIN_SEATS,
        })

    return {
        "generated": now.replace(microsecond=0).isoformat(),
        "criteria": {"min_row": MIN_ROW, "min_seats": MIN_SEATS, "from_hour": FROM_HOUR},
        "film": upcoming[0]["film"] if upcoming else "Odyssea",
        "film_link": next((e.get("filmLink") for e in upcoming if e.get("filmLink")), None),
        "screenings": out,
    }


# ---------------------------------------------------------------- vykreslení

def cz_date(iso):
    d = datetime.fromisoformat(iso)
    return f"{d.day}. {CZ_MONTHS[d.month]}"


def plural(n, one, few, many):
    return one if n == 1 else (few if n < 5 else many)


def seat_word(n):
    return f"{n} {plural(n, 'volné místo', 'volná místa', 'volných míst')}"


def row_strip(s):
    """Pásek řad 1..N: šířka buňky = řada, výplň = kolik je v ní volno."""
    cells = []
    top = max(int(r) for r in s["per_row"]) if s["per_row"] else 12
    top = max(top, s.get("max_row") or 12)
    for r in range(1, top + 1):
        free = s["per_row"].get(str(r), 0)
        good = r >= MIN_ROW
        state = "empty" if free == 0 else ("good" if good else "bad")
        title = f"řada {r}: {seat_word(free)}" + ("" if good else " (blízko plátna)")
        cells.append(
            f'<span class="cell {state}" title="{html.escape(title)}">'
            f'<span class="num">{r}</span></span>'
        )
    return '<div class="strip" aria-hidden="false">' + "".join(cells) + "</div>"


def screening_card(s):
    classes = ["shw"]
    if s["match"]:
        classes.append("hit")
    if not s["late"]:
        classes.append("early")
    if s["good_seats"] == 0:
        classes.append("none")

    if s["good_seats"] == 0:
        verdict = "nic od řady %d" % MIN_ROW
    else:
        verdict = seat_word(s["good_seats"])
        if s["best_block"] >= 2:
            verdict += f" · {s['best_block']} vedle sebe"

    tag = "" if s["late"] else f'<span class="tag">před {FROM_HOUR}:00</span>'
    detail = f'<p class="detail">{html.escape(s["detail"])}</p>' if s["detail"] else ""
    return f"""
      <article class="{' '.join(classes)}">
        <div class="shw-head">
          <a class="time" href="{html.escape(s['booking'])}" target="_blank" rel="noopener">{s['time']}</a>
          {tag}
          <span class="verdict">{html.escape(verdict)}</span>
        </div>
        {row_strip(s)}
        {detail}
        <p class="meta">v sále volno {s['free_regular']} z {s['hall_size'] - s['free_accessible']} · <a href="{html.escape(s['booking'])}" target="_blank" rel="noopener">koupit</a></p>
      </article>"""


def render_html(data):
    scr = data["screenings"]
    hits = [s for s in scr if s["match"]]
    considered = [s for s in scr if s["late"]]
    days = collections.OrderedDict()
    for s in scr:
        days.setdefault(s["date"], []).append(s)
    hit_days = sorted({s["date"] for s in hits})
    gen = datetime.fromisoformat(data["generated"])

    if hits:
        headline = f"{len(hits)} {plural(len(hits), 'termín', 'termíny', 'termínů')} projde"
        sub = (f"{len(hit_days)} {plural(len(hit_days), 'den', 'dny', 'dnů')} "
               f"s aspoň {MIN_SEATS} volnými místy od řady {MIN_ROW} po {FROM_HOUR}:00.")
        state = "yes"
    else:
        best = max((s["good_seats"] for s in considered), default=0)
        headline = "Zatím nic"
        sub = (f"Žádná z {len(considered)} večerních projekcí nemá {MIN_SEATS} volná místa "
               f"od řady {MIN_ROW}. Nejlepší nabídka: {seat_word(best)}.")
        state = "no"

    near = [s for s in considered if 0 < s["good_seats"] < MIN_SEATS]
    near_html = ""
    if near:
        items = "".join(
            f'<li><a href="{html.escape(s["booking"])}" target="_blank" rel="noopener">'
            f'{CZ_DAYS_SHORT[s["weekday"]]} {cz_date(s["date"])} {s["time"]}</a>'
            f' — {html.escape(s["detail"])}</li>'
            for s in near
        )
        near_html = f"""
      <section class="near">
        <h2>Blízko, ale málo míst</h2>
        <ul>{items}</ul>
      </section>"""

    day_blocks = []
    for date, group in days.items():
        d = datetime.fromisoformat(date)
        best = max(s["good_seats"] for s in group)
        day_state = "hit" if any(s["match"] for s in group) else ("some" if best else "none")
        day_blocks.append(f"""
      <section class="day {day_state}">
        <div class="day-rail">
          <span class="dow">{CZ_DAYS_SHORT[d.weekday()]}</span>
          <span class="dnum">{d.day}.</span>
          <span class="mon">{d.month}.</span>
        </div>
        <div class="day-body">{''.join(screening_card(s) for s in group)}</div>
      </section>""")

    film = html.escape(data["film"])
    film_link = data.get("film_link") or "https://www.cinemacity.cz"
    cinema = html.escape(scr[0]["cinema"]) if scr else ""
    hall = html.escape(scr[0]["auditorium"]) if scr else ""
    last_day = cz_date(scr[-1]["date"]) if scr else "—"

    return f"""<title>Odyssea 70mm Flora</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Oswald:wght@400;500;600&family=Source+Sans+3:ital,wght@0,400;0,600;1,400&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root {{
  --bg: #eceee9;
  --panel: #ffffff;
  --panel-2: #f5f6f3;
  --line: #d5d8d1;
  --ink: #16181a;
  --muted: #676d70;
  --accent: #b06412;
  --accent-soft: #f3e3d1;
  --yes: #24704f;
  --yes-soft: #d9ebe1;
  --dim: #a9aeb0;
  --shadow: 0 1px 2px rgba(20, 24, 26, .06), 0 8px 24px -18px rgba(20, 24, 26, .45);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #14161a;
    --panel: #1c1f24;
    --panel-2: #23272d;
    --line: #31363d;
    --ink: #e8eae6;
    --muted: #949ba1;
    --accent: #e9a54a;
    --accent-soft: #3b2c17;
    --yes: #62c79b;
    --yes-soft: #1d3a2b;
    --dim: #5b6268;
    --shadow: 0 1px 2px rgba(0, 0, 0, .5), 0 10px 30px -20px rgba(0, 0, 0, .8);
  }}
}}
:root[data-theme="dark"] {{
  --bg: #14161a;
  --panel: #1c1f24;
  --panel-2: #23272d;
  --line: #31363d;
  --ink: #e8eae6;
  --muted: #949ba1;
  --accent: #e9a54a;
  --accent-soft: #3b2c17;
  --yes: #62c79b;
  --yes-soft: #1d3a2b;
  --dim: #5b6268;
  --shadow: 0 1px 2px rgba(0, 0, 0, .5), 0 10px 30px -20px rgba(0, 0, 0, .8);
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 400 16px/1.55 "Source Sans 3", ui-sans-serif, system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
}}
.wrap {{ max-width: 61rem; margin: 0 auto; padding: clamp(1.25rem, 4vw, 3rem) clamp(1rem, 4vw, 2rem) 4rem; display: flex; flex-direction: column; gap: 1.75rem; }}
a {{ color: inherit; }}
h1, h2, h3, .time, .dnum, .kpi-n {{ font-family: Oswald, "Arial Narrow", sans-serif; font-weight: 500; text-wrap: balance; }}

.eyebrow {{ font: 500 .72rem/1.2 "IBM Plex Mono", ui-monospace, monospace; letter-spacing: .16em; text-transform: uppercase; color: var(--muted); display: flex; flex-wrap: wrap; gap: .5rem 1rem; }}
h1 {{ margin: .45rem 0 0; font-size: clamp(2rem, 6vw, 2.9rem); letter-spacing: .01em; line-height: 1.05; }}
h1 .film {{ color: var(--accent); }}
.lede {{ margin: .5rem 0 0; max-width: 34rem; color: var(--muted); }}

.verdict-box {{ background: var(--panel); border: 1px solid var(--line); border-left: 4px solid var(--dim); border-radius: 3px; padding: 1.1rem 1.25rem; box-shadow: var(--shadow); display: flex; flex-direction: column; gap: .3rem; }}
.verdict-box.yes {{ border-left-color: var(--yes); }}
.verdict-box.no {{ border-left-color: var(--accent); }}
.verdict-box h2 {{ margin: 0; font-size: 1.5rem; }}
.verdict-box p {{ margin: 0; color: var(--muted); }}

.kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr)); gap: .75rem; }}
.kpi {{ background: var(--panel); border: 1px solid var(--line); border-radius: 3px; padding: .8rem .9rem; }}
.kpi-n {{ display: block; font-size: 1.9rem; line-height: 1.1; font-variant-numeric: tabular-nums; }}
.kpi-l {{ font: 400 .74rem/1.3 "IBM Plex Mono", monospace; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }}

.near {{ background: var(--accent-soft); border: 1px solid var(--line); border-radius: 3px; padding: 1rem 1.25rem; }}
.near h2 {{ margin: 0 0 .4rem; font-size: 1.05rem; }}
.near ul {{ margin: 0; padding-left: 1.1rem; display: flex; flex-direction: column; gap: .25rem; }}
.near a {{ font-family: "IBM Plex Mono", monospace; font-size: .85rem; }}

.board {{ display: flex; flex-direction: column; gap: .5rem; }}
.board > h2 {{ margin: .75rem 0 .25rem; font-size: 1.15rem; }}
.day {{ display: grid; grid-template-columns: 4.25rem 1fr; gap: .75rem; background: var(--panel); border: 1px solid var(--line); border-radius: 3px; overflow: hidden; }}
.day.hit {{ border-color: var(--yes); }}
.day-rail {{ display: flex; flex-direction: column; justify-content: center; align-items: center; padding: .7rem .4rem; background: var(--panel-2); border-right: 1px solid var(--line); }}
.day.hit .day-rail {{ background: var(--yes-soft); }}
.dow {{ font: 500 .7rem/1 "IBM Plex Mono", monospace; letter-spacing: .12em; text-transform: uppercase; color: var(--muted); }}
.dnum {{ font-size: 1.5rem; line-height: 1.1; font-variant-numeric: tabular-nums; }}
.mon {{ font: 400 .72rem/1 "IBM Plex Mono", monospace; color: var(--muted); }}
.day-body {{ display: flex; flex-wrap: wrap; gap: .25rem 1.5rem; padding: .6rem .9rem .7rem 0; }}

.shw {{ min-width: 15rem; flex: 1 1 15rem; padding: .35rem 0; }}
.shw-head {{ display: flex; align-items: baseline; gap: .5rem; flex-wrap: wrap; }}
.time {{ font-size: 1.15rem; text-decoration: none; border-bottom: 1px solid var(--line); font-variant-numeric: tabular-nums; }}
.time:hover {{ border-bottom-color: var(--accent); color: var(--accent); }}
.shw.hit .time {{ color: var(--yes); border-bottom-color: var(--yes); }}
.verdict {{ font-size: .88rem; color: var(--muted); }}
.shw.hit .verdict {{ color: var(--yes); font-weight: 600; }}
.tag {{ font: 400 .66rem/1.5 "IBM Plex Mono", monospace; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); border: 1px solid var(--line); border-radius: 999px; padding: 0 .45rem; }}
.shw.early {{ opacity: .62; }}
.detail {{ margin: .3rem 0 0; font: 400 .8rem/1.4 "IBM Plex Mono", monospace; color: var(--ink); }}
.meta {{ margin: .2rem 0 0; font-size: .78rem; color: var(--muted); font-variant-numeric: tabular-nums; }}
.meta a {{ color: var(--accent); }}

.strip {{ display: flex; gap: 2px; margin-top: .4rem; }}
.cell {{ position: relative; flex: 1 1 auto; height: 1.15rem; border-radius: 1px; background: var(--panel-2); border: 1px solid var(--line); display: grid; place-items: center; }}
.cell .num {{ font: 400 .58rem/1 "IBM Plex Mono", monospace; color: var(--dim); }}
.cell.bad {{ background: var(--dim); border-color: var(--dim); }}
.cell.bad .num {{ color: var(--panel); }}
.cell.good {{ background: var(--yes); border-color: var(--yes); }}
.cell.good .num {{ color: var(--panel); }}

.legend {{ display: flex; flex-wrap: wrap; gap: .35rem 1.25rem; font-size: .8rem; color: var(--muted); }}
.legend span {{ display: inline-flex; align-items: center; gap: .4rem; }}
.swatch {{ width: .8rem; height: .8rem; border-radius: 1px; border: 1px solid var(--line); display: inline-block; }}
.swatch.good {{ background: var(--yes); border-color: var(--yes); }}
.swatch.bad {{ background: var(--dim); border-color: var(--dim); }}
.swatch.empty {{ background: var(--panel-2); }}

footer {{ border-top: 1px solid var(--line); padding-top: 1rem; font-size: .82rem; color: var(--muted); display: flex; flex-direction: column; gap: .3rem; }}
footer a {{ color: var(--accent); }}
@media (max-width: 30rem) {{
  .day {{ grid-template-columns: 3.4rem 1fr; }}
  .day-body {{ padding-right: .75rem; }}
}}
</style>

<div class="wrap">
  <header>
    <p class="eyebrow"><span>{cinema}</span><span>sál {hall}</span><span>70&nbsp;mm</span></p>
    <h1><span class="film">{film}</span><br>volná místa od řady {MIN_ROW}</h1>
    <p class="lede">Hlídané kritérium: aspoň {MIN_SEATS} volná místa v řadě {MIN_ROW} a dál, projekce od {FROM_HOUR}:00. Místa pro vozíčkáře a řady u plátna se nepočítají.</p>
  </header>

  <div class="verdict-box {state}">
    <h2>{html.escape(headline)}</h2>
    <p>{html.escape(sub)}</p>
  </div>

  <div class="kpis">
    <div class="kpi"><span class="kpi-n">{len(hits)}</span><span class="kpi-l">termínů projde</span></div>
    <div class="kpi"><span class="kpi-n">{len(considered)}</span><span class="kpi-l">projekcí od {FROM_HOUR}:00</span></div>
    <div class="kpi"><span class="kpi-n">{len(scr)}</span><span class="kpi-l">vypsáno celkem</span></div>
    <div class="kpi"><span class="kpi-n">{max((s['good_seats'] for s in considered), default=0)}</span><span class="kpi-l">nejvíc míst v jedné projekci</span></div>
  </div>
{near_html}
  <div class="board">
    <h2>Všechny vypsané projekce</h2>
    <p class="legend">
      <span><i class="swatch good"></i> volno v řadě {MIN_ROW}+</span>
      <span><i class="swatch bad"></i> volno blízko plátna</span>
      <span><i class="swatch empty"></i> obsazeno</span>
      <span>pásek = řady 1 → {scr[0]['max_row'] if scr else 12} od plátna</span>
    </p>
{''.join(day_blocks)}
  </div>

  <footer>
    <p>Aktualizováno {gen:%-d. %-m. %Y} v {gen:%H:%M} · rozpis kina končí {last_day}, další dny se teprve objeví.</p>
    <p><a href="{html.escape(film_link)}" target="_blank" rel="noopener">Stránka filmu</a> · <a href="https://github.com/DouJohn09/cinemacity-watchdog" target="_blank" rel="noopener">hlídač na GitHubu</a> posílá e-mail, když se místo uvolní.</p>
  </footer>
</div>
"""


def main():
    data = collect_data()
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    with open(OUT_HTML, "w", encoding="utf-8") as fh:
        fh.write(render_html(data))
    hits = [s for s in data["screenings"] if s["match"]]
    print(f"projekcí: {len(data['screenings'])} · projde kritériem: {len(hits)}")
    for s in hits:
        print(f"  {s['datetime']} — {s['good_seats']} míst, blok {s['best_block']} — {s['detail']}")
    print(f"zapsáno: {OUT_JSON}, {OUT_HTML}")


if __name__ == "__main__":
    main()

"""MLD-Archiv: komplette Liga-Historie von Fleaflicker sichern, damit nie etwas verloren geht.

Grundregeln
- Nur anwachsen: Es wird nie eine Datei geloescht.
- Abgeschlossenes ist unveraenderlich: Wochen, deren Spiele alle final sind, und Saisons,
  die vorbei sind, werden einmal geschrieben und danach nicht mehr angefasst.
- Laufendes wird aktualisiert (aktuelle Woche, aktuelle Kader, Standings der laufenden Saison).
- Listen mit IDs (Transaktionen, Trades) werden zusammengefuehrt, nie ersetzt.
- Rohes JSON genau so, wie Fleaflicker es liefert. Auswerten kann man spaeter.
- Fehler brechen den Lauf nicht ab: Sie landen im Log, fehlende Dateien werden beim naechsten Lauf
  erneut geholt.

Aufruf: python archive.py <zielordner>
"""
import html as htmlmod, json, os, re, sys, time, traceback, urllib.request, urllib.error
from datetime import datetime, timezone

LEAGUE_ID = 294292
FIRST_SEASON = 2019
UA = {"User-Agent": "mld-fa/1.0"}        # curl/Browser-UA werden von Fleaflicker geblockt
FLEA = "https://www.fleaflicker.com/api"
NOW = datetime.now(timezone.utc)
CUR_SEASON = NOW.year if NOW.month >= 8 else NOW.year - 1
ROOT = sys.argv[1] if len(sys.argv) > 1 else "archive"
ERRORS, STATS = [], {"written": 0, "unchanged": 0, "skipped_final": 0, "requests": 0}


def api(path, retries=5):
    STATS["requests"] += 1
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(f"{FLEA}/{path}", headers=UA), timeout=60) as r:
                time.sleep(0.25)
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (400, 404) or i == retries - 1:
                raise
            time.sleep(20 * (i + 1) if e.code in (403, 429) else 3)
        except (urllib.error.URLError, TimeoutError):
            if i == retries - 1:
                raise
            time.sleep(5)


def p(*parts):
    return os.path.join(ROOT, *[str(x) for x in parts])


def exists(*parts):
    return os.path.exists(p(*parts))


def save(data, *parts):
    """Schreibt nur, wenn sich der Inhalt geaendert hat (keine Leer-Commits)."""
    path = p(*parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    txt = json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            if f.read() == txt:
                STATS["unchanged"] += 1
                return
    with open(path, "w", encoding="utf-8") as f:
        f.write(txt)
    STATS["written"] += 1


def load(*parts, default=None):
    try:
        with open(p(*parts), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def guard(label, fn, *a):
    try:
        return fn(*a)
    except Exception as e:
        ERRORS.append(f"{label}: {type(e).__name__}: {e}")
        print("FEHLER", label, e)
        traceback.print_exc(limit=1)
        return None


def merge_by_id(existing, new, key="id"):
    """Fuehrt Listen zusammen. Bestehende Eintraege bleiben, neue Fassungen gewinnen.
    key: Feldname oder Funktion (fuer Eintraege ohne eigene ID)."""
    k = key if callable(key) else (lambda x: x.get(key))
    out = {str(k(x)): x for x in existing or [] if k(x) is not None}
    for x in new:
        if k(x) is not None:
            out[str(k(x))] = x
    return sorted(out.values(), key=lambda x: str(k(x)))


def tx_key(it):
    """Transaktionen haben keine ID -> Zeitstempel + Typ + Team + Spieler."""
    x = it.get("transaction") or {}
    return "|".join(str(v) for v in (it.get("timeEpochMilli"), x.get("type"), (x.get("team") or {}).get("id"),
                                       ((x.get("player") or {}).get("proPlayer") or {}).get("id")))


# ---------------- Liga-weite Dinge ----------------
def league_rules():
    d = api(f"FetchLeagueRules?sport=NFL&league_id={LEAGUE_ID}")
    save(d, "league", "rules_current.json")
    # Datierte Fassung nur, wenn sich die Regeln geaendert haben -> Regelhistorie
    hist = sorted(f for f in os.listdir(p("league")) if f.startswith("rules_20"))
    last = load("league", hist[-1]) if hist else None
    if last != d:
        save(d, "league", f"rules_{NOW:%Y-%m-%d}.json")


def transactions():
    """Alle Transaktionen, rueckwaerts gepaged, mit dem Bestand zusammengefuehrt."""
    have = load("league", "transactions.json", default=[])
    known = {tx_key(x) for x in have}
    items, offset = [], 0
    while True:
        d = api(f"FetchLeagueTransactions?sport=NFL&league_id={LEAGUE_ID}&result_offset={offset}")
        page = d.get("items") or []
        if not page:
            break
        items += page
        # Sobald eine ganze Seite schon bekannt ist, ist der Rest auch bekannt
        if have and all(tx_key(x) in known for x in page):
            break
        nxt = d.get("resultOffsetNext")
        if not nxt or nxt <= offset:
            break
        offset = nxt
    merged = merge_by_id(have, items, tx_key)
    save(merged, "league", "transactions.json")
    print(f"Transaktionen: {len(items)} geholt, gesamt {len(merged)}")


def trades():
    for flt in ("TRADES_COMPLETED", "TRADES_UNDER_REVIEW"):
        have = load("league", f"trades_{flt.lower()}.json", default=[])
        items, offset = [], 0
        while True:
            d = api(f"FetchTrades?sport=NFL&league_id={LEAGUE_ID}&filter={flt}&result_offset={offset}")
            page = d.get("trades") or []
            items += page
            nxt = d.get("resultOffsetNext")
            if not page or not nxt or nxt <= offset:
                break
            offset = nxt
        save(merge_by_id(have, items), "league", f"trades_{flt.lower()}.json")


def team_ids(season):
    st = load("seasons", season, "standings.json") or {}
    ids = []
    for div in st.get("divisions") or []:
        ids += [t["id"] for t in div.get("teams") or []]
    return ids


# ---------------- pro Saison ----------------
def season_done(season):
    return season < CUR_SEASON and exists("seasons", season, "_complete.json")


def standings(season):
    if season_done(season):
        return
    save(api(f"FetchLeagueStandings?sport=NFL&league_id={LEAGUE_ID}&season={season}"), "seasons", season, "standings.json")


def weeks(season):
    """Scoreboard + jeder Boxscore jeder Woche. Finale Wochen werden nie neu geholt."""
    all_final = True
    for week in range(1, 19):
        wdir = ("seasons", season, "weeks", f"{week:02d}")
        if exists(*wdir, "_final.json"):
            STATS["skipped_final"] += 1
            continue
        sb = api(f"FetchLeagueScoreboard?sport=NFL&league_id={LEAGUE_ID}&season={season}&scoring_period={week}")
        games = sb.get("games") or []
        if not games:
            break                                         # Saison zu Ende (oder Woche noch ohne Spiele)
        started = any(g.get("isFinalScore") or g.get("isInProgress") for g in games)
        if not started:
            all_final = False
            break                                         # Zukunft: nichts zu archivieren
        save(sb, *wdir, "scoreboard.json")
        ok = True
        for g in games:
            r = guard(f"boxscore {season}/{week}/{g.get('id')}",
                      lambda: api(f"FetchLeagueBoxscore?sport=NFL&league_id={LEAGUE_ID}"
                                  f"&fantasy_game_id={g['id']}&scoring_period={week}"))
            if r is None:
                ok = False
                continue
            save(r, *wdir, "boxscores", f"{g['id']}.json")
        if ok and all(g.get("isFinalScore") for g in games):
            save({"archived_utc": NOW.strftime("%Y-%m-%d %H:%M"), "games": len(games)}, *wdir, "_final.json")
        else:
            all_final = False
    return all_final


def rosters(season):
    """Kader aller Teams. Vergangene Saisons: Endstand (einmal). Laufende: aktueller Stand,
    zusaetzlich datierte Tages-Snapshots, damit auch Zwischenstaende erhalten bleiben."""
    if season_done(season):
        return
    for tid in team_ids(season):
        if season < CUR_SEASON and exists("seasons", season, "rosters", f"{tid}.json"):
            continue
        r = guard(f"roster {season}/{tid}",
                  lambda: api(f"FetchRoster?sport=NFL&league_id={LEAGUE_ID}&team_id={tid}&season={season}"))
        if r is None:
            continue
        save(r, "seasons", season, "rosters", f"{tid}.json")   # jede Fassung bleibt in der Git-Historie


def picks(season):
    if season != CUR_SEASON:
        return
    for tid in team_ids(season):
        r = guard(f"picks {tid}", lambda: api(f"FetchTeamPicks?sport=NFL&league_id={LEAGUE_ID}&team_id={tid}"))
        if r is not None:
            save(r, "seasons", season, "picks", f"{tid}.json")   # Historie: Git


def page(url):
    STATS["requests"] += 1
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=60) as r:
                time.sleep(0.25)
                return r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError):
            if i == 3:
                raise
            time.sleep(10)


DRAFT_HEAD = re.compile(r"Draft #(\d+)\s+(?:Draft #\d+\s+)*(\d{4})")


def draft_text(h):
    """Sichtbarer Text der Draftseite (zum Pruefen: richtige Saison, richtiger Draft, nicht doppelt)."""
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", h, flags=re.S)
    return re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", t)))


def drafts(season):
    """Draftboards als HTML (die API-Variante liefert 400). Gespeichert wird nur, was nachweislich
    der gefragte Draft der gefragten Saison ist -- Fleaflicker liefert sonst still etwas anderes."""
    prev_body = None
    for idx in range(0, 4):
        if season < CUR_SEASON and exists("seasons", season, "drafts", f"draft_{idx}.html"):
            prev_body = draft_text(open(p("seasons", season, "drafts", f"draft_{idx}.html"), encoding="utf-8").read())
            continue
        h = page(f"https://www.fleaflicker.com/nfl/leagues/{LEAGUE_ID}/drafts?draftIndex={idx}&season={season}")
        t = draft_text(h)
        m = DRAFT_HEAD.search(t)
        # Ab 2023 Listenansicht ("Round 1 ... 1.1 #1"), 2020-2022 Board-Ansicht ("1.1 RB - ...")
        first = re.search(r"\b1\.1\b", t)
        body = t[first.start():] if first and len(t) - first.start() > 500 else ""
        if not m or int(m.group(2)) != season or int(m.group(1)) != idx + 1 or not body or body == prev_body:
            if idx == 0:
                ERRORS.append(f"draft {season}/0: Seite passt nicht (Kopf: {m.group(0) if m else '-'})")
            break
        prev_body = body
        path = p("seasons", season, "drafts", f"draft_{idx}.html")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
        if old != h:
            open(path, "w", encoding="utf-8").write(h)
            STATS["written"] += 1


def run_season(season):
    print(f"--- Saison {season} ---")
    guard(f"standings {season}", standings, season)
    guard(f"drafts {season}", drafts, season)
    if season_done(season):
        print("abgeschlossen, uebersprungen")
        return
    fin = guard(f"weeks {season}", weeks, season)
    guard(f"rosters {season}", rosters, season)
    guard(f"picks {season}", picks, season)
    if season < CUR_SEASON and fin and not any(str(season) in e for e in ERRORS):
        save({"archived_utc": NOW.strftime("%Y-%m-%d %H:%M")}, "seasons", season, "_complete.json")


def manifest():
    """Uebersicht zum Pruefen: was liegt pro Saison im Archiv."""
    m = {"updated_utc": NOW.strftime("%Y-%m-%d %H:%M"), "league_id": LEAGUE_ID, "seasons": {}}
    for s in range(FIRST_SEASON, CUR_SEASON + 1):
        wdir = p("seasons", s, "weeks")
        wk = sorted(os.listdir(wdir)) if os.path.isdir(wdir) else []
        m["seasons"][s] = {
            "complete": exists("seasons", s, "_complete.json"),
            "weeks": len(wk),
            "weeks_final": sum(exists("seasons", s, "weeks", w, "_final.json") for w in wk),
            "boxscores": sum(len(os.listdir(p("seasons", s, "weeks", w, "boxscores")))
                             for w in wk if os.path.isdir(p("seasons", s, "weeks", w, "boxscores"))),
            "rosters": len(os.listdir(p("seasons", s, "rosters"))) if os.path.isdir(p("seasons", s, "rosters")) else 0,
            "drafts": sum(exists("seasons", s, "drafts", f"draft_{i}.html") for i in range(4)),
        }
    m["transactions"] = len(load("league", "transactions.json", default=[]))
    m["trades_completed"] = len(load("league", "trades_trades_completed.json", default=[]))
    m["last_run"] = {**STATS, "errors": ERRORS}
    save(m, "MANIFEST.json")
    return m


def main():
    os.makedirs(p("league"), exist_ok=True)
    guard("rules", league_rules)
    for s in range(FIRST_SEASON, CUR_SEASON + 1):
        run_season(s)
    guard("transactions", transactions)
    guard("trades", trades)
    m = manifest()
    print(json.dumps(m, indent=1))
    # Exit 0 auch bei Einzelfehlern: Teilergebnisse sollen trotzdem gesichert werden.


if __name__ == "__main__":
    main()

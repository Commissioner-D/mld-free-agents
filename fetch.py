"""MLD Free Agents: Fleaflicker FA-Liste + nflverse-Trends (Snaps, Targets, IDP)."""
import csv, io, json, os, re, time, unicodedata, urllib.request, urllib.error
from datetime import datetime, timezone

LEAGUE_ID = 294292
UA = {"User-Agent": "mld-fa/1.0"}
FLEA = "https://www.fleaflicker.com/api/FetchPlayerListing"
NFLV = "https://github.com/nflverse/nflverse-data/releases/download"
TREND_WEEKS = 4
NOW = datetime.now(timezone.utc)
SEASON = NOW.year if NOW.month >= 8 else NOW.year - 1
TEAM_MAP = {"LAR": "LA", "JAC": "JAX", "WSH": "WAS"}   # Fleaflicker -> nflverse
# Positionsgruppen fuer Plausibilitaetscheck bei Fallback-Zuordnung (OLB zaehlt zu beiden)
GROUPS = {"QB": {"QB"}, "RB": {"RB", "FB", "HB"}, "WR": {"WR"}, "TE": {"TE"}, "K": {"K"},
          "DL": {"DE", "DT", "NT", "DL", "EDR", "IL", "OLB"},
          "LB": {"LB", "ILB", "MLB", "OLB"},
          "DB": {"CB", "S", "SS", "FS", "DB"}}


def same_group(a, b):
    return any(a in g and b in g for g in GROUPS.values())
SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def get(url, retries=3):
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read()
        except urllib.error.URLError:
            if i == retries - 1:
                raise
            time.sleep(3)


def norm(name):
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    n = re.sub(r"[.'\-]", "", n)
    n = SUFFIX.sub("", n)
    return re.sub(r"\s+", " ", n).strip()


def val(obj):
    return (obj or {}).get("value")


# ---------- Fleaflicker ----------
def fetch_free_agents():
    players, offset = [], 0
    while True:
        d = json.loads(get(f"{FLEA}?sport=NFL&league_id={LEAGUE_ID}&filter.free_agent_only=true"
                           f"&sort=SORT_SEASON_TOTAL&result_offset={offset}"))
        batch = d.get("players", [])
        players += batch
        nxt = d.get("resultOffsetNext")
        if not batch or not nxt or nxt <= offset or offset > 3000:
            break
        offset = nxt
        time.sleep(0.4)
    out = []
    for p in players:
        pp = p["proPlayer"]
        last = {x.get("duration"): val(x.get("value")) for x in p.get("lastX", [])}
        news = (pp.get("news") or [{}])[0]
        rank_pos = ((p.get("rankFantasy") or {}).get("positions") or [{}])[0]
        out.append({
            "fl_id": pp["id"],
            "name": pp["nameFull"],
            "pos": pp.get("position"),
            "elig": pp.get("positionEligibility"),
            "team": pp.get("proTeamAbbreviation"),
            "bye": pp.get("nflByeWeek"),
            "injury": (pp.get("injury") or {}).get("typeFull"),
            "pct_owned": pp.get("percentOwnedRatio"),
            "pts_last1": last.get(1), "pts_last3": last.get(3), "pts_last5": last.get(5),
            "pts_avg": val(p.get("seasonAverage")), "pts_total": val(p.get("seasonTotal")),
            "proj": val(p.get("viewingProjectedPoints")),
            "pos_rank": rank_pos.get("ordinal"),
            "news": {"time": news.get("timeEpochMilli"), "text": news.get("contents"),
                     "analysis": news.get("analysis")} if news else None,
        })
    return out


# ---------- nflverse ----------
def read_csv(path):
    return list(csv.DictReader(io.StringIO(get(f"{NFLV}/{path}").decode("utf-8"))))


def num(x):
    try:
        return round(float(x), 3)
    except (TypeError, ValueError):
        return None


def build_nflverse():
    snaps = read_csv(f"snap_counts/snap_counts_{SEASON}.csv")
    stats = read_csv(f"stats_player/stats_player_week_{SEASON}.csv")
    snaps = [r for r in snaps if r["game_type"] == "REG"]
    stats = [r for r in stats if r["season_type"] == "REG"]
    weeks = sorted({int(r["week"]) for r in snaps} | {int(r["week"]) for r in stats})
    keep = weeks[-TREND_WEEKS:]
    agg = {}  # (normname, team) -> {week: {...}}

    pos_of = {}

    def slot(name, team, wk, pos):
        key = (norm(name), team)
        if pos:
            pos_of.setdefault(key, pos)
        return agg.setdefault(key, {}).setdefault(wk, {})

    for r in snaps:
        wk = int(r["week"])
        if wk in keep:
            s = slot(r["player"], r["team"], wk, r.get("position"))
            s["off_pct"] = num(r["offense_pct"]); s["def_pct"] = num(r["defense_pct"])
            s["st_pct"] = num(r["st_pct"])
    for r in stats:
        wk = int(r["week"])
        if wk in keep:
            s = slot(r["player_display_name"], r["team"], wk, r.get("position"))
            for k in ["targets", "target_share", "wopr", "carries", "receptions",
                      "def_tackles_solo", "def_tackle_assists", "def_sacks", "def_pass_defended"]:
                if r.get(k) not in (None, "", "NA"):
                    s[k] = num(r[k])
    by_name = {}
    for (n, t) in agg:
        by_name.setdefault(n, []).append(t)
    return agg, by_name, pos_of, keep


def trends(weekly, weeks):
    out = {}
    for k in ["off_pct", "def_pct", "st_pct", "targets", "target_share", "wopr", "carries",
              "def_tackles_solo", "def_tackle_assists", "def_sacks", "def_pass_defended"]:
        series = [weekly.get(w, {}).get(k) for w in weeks]
        if any(v for v in series):          # nur Null/None -> weglassen
            out[k] = series
    return out


def main():
    equiv = {}
    if os.path.exists("equivalents.json"):
        equiv = {k: v for k, v in json.load(open("equivalents.json")).items() if not k.startswith("_")}
    fas = fetch_free_agents()
    agg, by_name, pos_of, weeks = build_nflverse()
    unmatched, review = [], []
    for p in fas:
        n = norm(equiv.get(p["name"], p["name"]))
        t = TEAM_MAP.get(p["team"], p["team"])
        key, how = None, None
        if (n, t) in agg:
            key, how = (n, t), "exact" if p["name"] not in equiv else "equiv"
        elif len(by_name.get(n, [])) == 1:                     # Teamwechsel/FA: Name eindeutig
            cand = (n, by_name[n][0])
            if same_group(p["pos"] or "", pos_of.get(cand, "")):
                key, how = cand, "team_change"
                review.append(f'{p["name"]} ({p["pos"]}, {p["team"]}) -> {cand[0]} ({pos_of.get(cand)}, {cand[1]})')
        if key:
            p["trend"] = trends(agg[key], weeks)
            p["match"] = how
        else:
            p["trend"] = None
            if (p.get("pts_total") or 0) > 0:
                unmatched.append(f'{p["name"]} ({p["pos"]}, {p["team"]})')
    os.makedirs("data", exist_ok=True)
    result = {"generated_utc": NOW.strftime("%Y-%m-%d %H:%M"), "season": SEASON,
              "trend_weeks": weeks, "count": len(fas),
              "unmatched_with_points": unmatched, "team_change_matches": review,
              "players": fas}
    json.dump(result, open("data/free_agents.json", "w"), ensure_ascii=False, separators=(",", ":"))
    print(f"{len(fas)} FAs, Wochen {weeks}, {sum(1 for p in fas if p['trend'])} mit Trend, "
          f"{len(unmatched)} ohne Match (mit Punkten), {len(review)} Teamwechsel-Matches")


if __name__ == "__main__":
    main()

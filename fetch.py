"""MLD Free Agents: Fleaflicker FA-Liste + nflverse-Trends (Snaps, Targets, IDP)."""
import csv, gzip, io, json, os, re, time, unicodedata, urllib.request, urllib.error
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
          "DB": {"CB", "S", "SS", "FS", "DB", "SAF"}}


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
    # Red Zone aus Play-by-Play (yardline_100 <= 20): Targets + Carries pro Spieler/Woche
    gsis = {r["player_id"]: (norm(r["player_display_name"]), r["team"]) for r in stats}
    try:
        raw = gzip.decompress(get(f"{NFLV}/pbp/play_by_play_{SEASON}.csv.gz")).decode("utf-8")
        for r in csv.DictReader(io.StringIO(raw)):
            if r.get("season_type") != "REG" or not r.get("week") or int(r["week"]) not in keep:
                continue
            try:
                if float(r.get("yardline_100") or 99) > 20:
                    continue
            except ValueError:
                continue
            pt, wk = r.get("play_type"), int(r["week"])
            pid = r.get("receiver_player_id") if pt == "pass" else r.get("rusher_player_id") if pt == "run" else None
            if pid and pid in gsis and gsis[pid] in agg:
                s = agg[gsis[pid]].setdefault(wk, {})
                s["rz_opps"] = s.get("rz_opps", 0) + 1
    except Exception as e:                            # pbp faellt aus -> Rest laeuft weiter
        print("pbp nicht verfuegbar:", e)
    by_name = {}
    for (n, t) in agg:
        by_name.setdefault(n, []).append(t)
    return agg, by_name, pos_of, keep


def build_bio():
    bio, by_name = {}, {}
    for r in read_csv("players/players.csv"):
        if r.get("last_season") not in (str(SEASON), str(SEASON - 1)):
            continue
        key = (norm(r["display_name"]), r.get("latest_team"))
        bio[key] = {"birth": r.get("birth_date") or None, "draft_round": r.get("draft_round") or None,
                    "draft_pick": r.get("draft_pick") or None, "exp": r.get("years_of_experience") or None,
                    "rookie": r.get("rookie_season") == str(SEASON), "pos": r.get("position")}
        by_name.setdefault(key[0], []).append(key)
    return bio, by_name


def age(birth):
    try:
        b = datetime.strptime(birth, "%Y-%m-%d")
        return round((NOW.replace(tzinfo=None) - b).days / 365.25, 1)
    except (TypeError, ValueError):
        return None


def trends(weekly, weeks):
    out = {}
    for w in weeks:                                   # Opportunities = Carries + Targets
        wk = weekly.get(w)
        if wk and ("carries" in wk or "targets" in wk):
            wk["opps"] = (wk.get("carries") or 0) + (wk.get("targets") or 0)
    for k in ["off_pct", "def_pct", "st_pct", "targets", "target_share", "wopr", "carries", "opps", "rz_opps",
              "def_tackles_solo", "def_tackle_assists", "def_sacks", "def_pass_defended"]:
        series = [weekly.get(w, {}).get(k) for w in weeks]
        if any(v for v in series):          # nur Null/None -> weglassen
            out[k] = series
    return out



# ---------- Scoring ----------
SCORE_GROUP = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "EDR": "DL", "IL": "DL",
               "EDR/IL": "DL", "LB": "LB", "S": "S"}          # CB, DB, K: keine Wertung, bleiben aber drin
HOT_N = {"QB": 5, "RB": 8, "WR": 10, "TE": 5, "DL": 6, "LB": 8, "S": 6}
WEIGHTS = {"form1": .18, "form3": .18, "avg": .08, "role": .20, "role_trend": .14, "rz": .10, "own": .04, "proj": .08}
NEWS_POS = re.compile(r"\b(will start|expected to start|in line to start|starting role|named (the )?starter|"
                      r"promot|first-team|first team|expanded role|increased role|bigger role|"
                      r"lead back|atop the depth chart|top of the depth chart|take over|takes over|"
                      r"fill in|in place of|step into|stepped into|every-down|three-down)", re.I)
BAD_INJ = {"Out", "Injured Reserve", "IR", "Doubtful", "Suspended", "PUP"}


def last_and_prior(series):
    vals = [v for v in (series or []) if v is not None]
    if not vals:
        return None, None
    last = vals[-1]
    prior = vals[:-1]
    return last, (last - sum(prior) / len(prior)) if prior else None


def pct_rank(values):
    """Perzentil 0..1 je Wert (None -> 0)."""
    present = sorted(v for v in values if v is not None)
    n = len(present)
    out = []
    for v in values:
        if v is None or n == 0:
            out.append(0.0)
        else:
            below = sum(1 for x in present if x < v)
            out.append(round(below / max(n - 1, 1), 3))
    return out


def score_players(fas):
    week_ms = 7 * 24 * 3600 * 1000
    now_ms = NOW.timestamp() * 1000
    for p in fas:
        g = SCORE_GROUP.get(p["pos"])
        p["group"] = g
        tr = p.get("trend") or {}
        is_idp = g in ("DL", "LB", "S")
        snap_key = "def_pct" if is_idp else "off_pct"
        role_last, role_delta = last_and_prior(tr.get(snap_key))
        ts_last, _ = last_and_prior(tr.get("target_share"))
        opps_last, opps_delta = last_and_prior(tr.get("opps"))
        role = role_last
        if g in ("WR", "TE") and ts_last is not None:
            role = 0.5 * (role_last or 0) + 0.5 * min(ts_last * 3, 1)   # Target Share 33 % = voll
        elif g == "RB":                                 # Touches zaehlen, Snaps allein (Fullback) kaum
            role = 0.2 * (role_last or 0) + 0.8 * min((opps_last or 0) / 18, 1)
        p["m"] = {"form1": p.get("pts_last1"), "form3": p.get("pts_last3"), "avg": p.get("pts_avg"),
                  "role": role, "role_trend": role_delta, "own": p.get("pct_owned"), "proj": p.get("proj"),
                  "snap_last": role_last, "ts_last": ts_last, "opps_last": opps_last,
                  "rz": (sum(v for v in (tr.get("rz_opps") or [])[-3:] if v) or None) if g in ("RB", "WR", "TE") else None,
                  "rz_last": ([v for v in (tr.get("rz_opps") or []) if v is not None] or [None])[-1]}
        flags = []
        if role_delta is not None and role_delta >= 0.20 and (role_last or 0) >= 0.50:
            flags.append("SNAP_JUMP")
        if g in ("WR", "TE") and ts_last is not None and ts_last >= 0.20:
            flags.append("TARGETS")
        if g == "RB" and opps_last is not None and opps_last >= 12:
            flags.append("WORKLOAD")
        if g in ("RB", "WR", "TE") and (p["m"]["rz_last"] or 0) >= 3:
            flags.append("RED_ZONE")
        if (g in ("LB", "S") and role_last and role_last >= 0.90) or (g == "DL" and role_last and role_last >= 0.80):
            flags.append("EVERY_DOWN")
        n = p.get("news") or {}
        if n.get("time") and now_ms - float(n["time"]) <= week_ms and \
                NEWS_POS.search(f'{n.get("text") or ""} {n.get("analysis") or ""}'):
            flags.append("NEWS")
        if p.get("age") is not None and p["age"] <= 24.5 and role_delta is not None and role_delta >= 0.10 \
                and (role_last or 0) >= 0.40:
            flags.append("YOUNG_RISER")
        if p.get("injury") in BAD_INJ:
            flags.append("INJURED")
        p["flags"] = flags

    groups = {}
    for p in fas:
        if p["group"]:
            groups.setdefault(p["group"], []).append(p)
    for g, ps in groups.items():
        ranks = {k: pct_rank([p["m"][k] for p in ps]) for k in WEIGHTS}
        for i, p in enumerate(ps):
            sc = 100 * sum(WEIGHTS[k] * ranks[k][i] for k in WEIGHTS)
            if g == "RB":                              # wenig Touches (Fullback, Garbage-Time) -> nach hinten
                o = [v for v in ((p.get("trend") or {}).get("opps") or []) if v is not None]
                avg_o = sum(o) / len(o) if o else 0
                sc *= min(1.0, 0.5 + avg_o / 10)
            p["score"] = round(sc, 1)
        ps.sort(key=lambda p: -p["score"])
        for i, p in enumerate(ps):
            p["group_rank"] = i + 1
            trig = [f for f in p["flags"] if f != "INJURED"]
            rising = (p["m"]["role_trend"] or 0) >= 0.10 and (p["m"]["snap_last"] or 0) >= 0.40
            if i < HOT_N[g] and p["score"] >= 70:
                p["tier"] = "hot"
            elif trig and p["score"] >= 45:
                p["tier"] = "signal"
            elif p["score"] >= 65 or trig or rising:      # schwache Signale: nicht verlieren, aber nur Radar
                p["tier"] = "radar"
            else:
                p["tier"] = "rest"
    for p in fas:
        if not p["group"]:
            p["score"], p["group_rank"], p["tier"] = None, None, "unscored"


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
    bio, bio_by_name = build_bio()
    for p in fas:
        n = norm(equiv.get(p["name"], p["name"]))
        t = TEAM_MAP.get(p["team"], p["team"])
        b = bio.get((n, t))
        if not b and len(bio_by_name.get(n, [])) == 1:
            cand = bio[bio_by_name[n][0]]
            if same_group(p["pos"] or "", cand.get("pos") or ""):
                b = cand
        p["age"] = age(b["birth"]) if b else None
        p["draft"] = f'R{b["draft_round"]}/{b["draft_pick"]}' if b and b.get("draft_round") else ("UDFA" if b else None)
        p["exp"] = int(b["exp"]) if b and b.get("exp") not in (None, "") else None
        p["rookie"] = bool(b and b.get("rookie"))
    score_players(fas)
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

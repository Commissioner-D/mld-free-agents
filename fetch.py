"""MLD Free Agents: Fleaflicker FA-Liste + eigener Kader + nflverse-Trends.

Quellen: Fleaflicker API (Punkte im MLD-Scoring, News, Ownership),
nflverse (Snaps, Targets, IDP-Stats, Play-by-Play fuer Red Zone und Defense-Rollen).
Ausgabe: data/free_agents.json
"""
import csv, gzip, io, json, os, re, time, unicodedata, urllib.request, urllib.error
from datetime import datetime, timezone

LEAGUE_ID = 294292
MY_TEAM_ID = 1476731                     # Tegetthoff Admirals
UA = {"User-Agent": "mld-fa/1.0"}        # curl/Browser-UA werden von Fleaflicker geblockt
FLEA = "https://www.fleaflicker.com/api"
NFLV = "https://github.com/nflverse/nflverse-data/releases/download"
TREND_WEEKS = 4
NOW = datetime.now(timezone.utc)
SEASON = NOW.year if NOW.month >= 8 else NOW.year - 1
TEAM_MAP = {"LAR": "LA", "JAC": "JAX", "WSH": "WAS"}   # Fleaflicker -> nflverse
GROUPS = {"QB": {"QB"}, "RB": {"RB", "FB", "HB"}, "WR": {"WR"}, "TE": {"TE"}, "K": {"K"},
          "DL": {"DE", "DT", "NT", "DL", "EDR", "IL", "OLB"},
          "LB": {"LB", "ILB", "MLB", "OLB"},
          "DB": {"CB", "S", "SS", "FS", "DB", "SAF"}}
SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


# ---------- Helfer ----------
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


def same_group(a, b):
    return any(a in g and b in g for g in GROUPS.values())


def val(obj):
    return (obj or {}).get("value")


def num(x):
    try:
        return round(float(x), 3)
    except (TypeError, ValueError):
        return None


# ---------- Fleaflicker ----------
def parse_player(p, slot=None):
    pp = p["proPlayer"]
    last = {x.get("duration"): val(x.get("value")) for x in p.get("lastX", [])}
    news = (pp.get("news") or [{}])[0]
    rank_pos = ((p.get("rankFantasy") or {}).get("positions") or [{}])[0]
    return {
        "fl_id": pp["id"], "name": pp["nameFull"], "pos": pp.get("position"),
        "team": pp.get("proTeamAbbreviation"), "bye": pp.get("nflByeWeek"),
        "injury": (pp.get("injury") or {}).get("typeFull"),
        "pct_owned": pp.get("percentOwnedRatio"),
        "pts_last1": last.get(1), "pts_last3": last.get(3), "pts_last5": last.get(5),
        "pts_avg": val(p.get("seasonAverage")), "pts_total": val(p.get("seasonTotal")),
        "proj": val(p.get("viewingProjectedPoints")), "pos_rank": rank_pos.get("ordinal"),
        "news": {"time": news.get("timeEpochMilli"), "text": news.get("contents"),
                 "analysis": news.get("analysis")} if news else None,
        "mine": slot is not None, "slot": slot,
    }


def fetch_free_agents():
    players, offset = [], 0
    while True:
        d = json.loads(get(f"{FLEA}/FetchPlayerListing?sport=NFL&league_id={LEAGUE_ID}"
                           f"&filter.free_agent_only=true&sort=SORT_SEASON_TOTAL&result_offset={offset}"))
        batch = d.get("players", [])
        players += batch
        nxt = d.get("resultOffsetNext")
        if not batch or not nxt or nxt <= offset or offset > 3000:
            break
        offset = nxt
        time.sleep(0.4)
    return [parse_player(p) for p in players]


def fetch_my_roster():
    d = json.loads(get(f"{FLEA}/FetchRoster?sport=NFL&league_id={LEAGUE_ID}&team_id={MY_TEAM_ID}"))
    out = []
    for g in d.get("groups", []):
        slot = {"START": "Start", "INJURED": "IR", "TAXI": "Taxi"}.get(g.get("group"), "Bank")
        for s in g.get("slots", []):
            lp = s.get("leaguePlayer")
            if lp and lp.get("proPlayer"):
                out.append(parse_player(lp, slot))
    return out


# ---------- nflverse ----------
def read_csv(path):
    return list(csv.DictReader(io.StringIO(get(f"{NFLV}/{path}").decode("utf-8"))))


TACKLE_COLS = ["solo_tackle_1_player_id", "solo_tackle_2_player_id", "assist_tackle_1_player_id",
               "assist_tackle_2_player_id", "assist_tackle_3_player_id", "assist_tackle_4_player_id",
               "tackle_with_assist_1_player_id", "tackle_with_assist_2_player_id"]


def build_nflverse(players_csv):
    pfr_name = {r["pfr_id"]: r["display_name"] for r in players_csv if r.get("pfr_id")}
    snaps = [r for r in read_csv(f"snap_counts/snap_counts_{SEASON}.csv") if r["game_type"] == "REG"]
    stats = [r for r in read_csv(f"stats_player/stats_player_week_{SEASON}.csv") if r["season_type"] == "REG"]
    weeks = sorted({int(r["week"]) for r in snaps} | {int(r["week"]) for r in stats})
    keep = weeks[-TREND_WEEKS:]
    agg, pos_of, season = {}, {}, {}   # season: Saison-Summen fuer Defense-Profil

    def slot(name, team, wk, pos):
        key = (norm(name), team)
        if pos:
            pos_of.setdefault(key, pos)
        return agg.setdefault(key, {}).setdefault(wk, {})

    for r in snaps:
        wk = int(r["week"])
        r["player"] = pfr_name.get(r.get("pfr_player_id"), r["player"])   # Snap-Namen = Stats-Namen
        key = (norm(r["player"]), r["team"])
        sd = season.setdefault(key, {"def_snaps": 0, "tk": 0, "run": 0, "depth": [], "qbh": 0, "tfl": 0})
        sd["def_snaps"] += int(float(r["defense_snaps"] or 0))
        if wk in keep:
            s = slot(r["player"], r["team"], wk, r.get("position"))
            s["off_pct"] = num(r["offense_pct"]); s["def_pct"] = num(r["defense_pct"]); s["st_pct"] = num(r["st_pct"])
    for r in stats:
        wk = int(r["week"])
        if wk in keep:
            s = slot(r["player_display_name"], r["team"], wk, r.get("position"))
            for k in ["targets", "target_share", "wopr", "carries", "receptions",
                      "def_tackles_solo", "def_tackle_assists", "def_sacks", "def_pass_defended"]:
                if r.get(k) not in (None, "", "NA"):
                    s[k] = num(r[k])

    # Play-by-Play: Red Zone (Offense) + Tackle-Profil, QB-Hits, TFL (Defense)
    gsis = {r["player_id"]: (norm(r["player_display_name"]), r["team"]) for r in stats}
    try:
        raw = gzip.decompress(get(f"{NFLV}/pbp/play_by_play_{SEASON}.csv.gz")).decode("utf-8")
        for r in csv.DictReader(io.StringIO(raw)):
            if r.get("season_type") != "REG" or not r.get("week"):
                continue
            wk, pt = int(r["week"]), r.get("play_type")
            yl = num(r.get("yardline_100"))
            if wk in keep and yl is not None and yl <= 20:
                pid = r.get("receiver_player_id") if pt == "pass" else r.get("rusher_player_id") if pt == "run" else None
                if pid in gsis and gsis[pid] in agg:
                    s = agg[gsis[pid]].setdefault(wk, {})
                    s["rz_opps"] = s.get("rz_opps", 0) + 1
            for pid in {r.get(c) for c in TACKLE_COLS if r.get(c)}:
                if pid in gsis:
                    sd = season.setdefault(gsis[pid], {"def_snaps": 0, "tk": 0, "run": 0, "depth": [], "qbh": 0, "tfl": 0})
                    sd["tk"] += 1
                    sd["run"] += pt == "run"
                    y = num(r.get("yards_gained"))
                    if y is not None:
                        sd["depth"].append(y)
            for c, k in [("qb_hit_1_player_id", "qbh"), ("qb_hit_2_player_id", "qbh"),
                         ("tackle_for_loss_1_player_id", "tfl"), ("tackle_for_loss_2_player_id", "tfl")]:
                pid = r.get(c)
                if pid in gsis:
                    sd = season.setdefault(gsis[pid], {"def_snaps": 0, "tk": 0, "run": 0, "depth": [], "qbh": 0, "tfl": 0})
                    sd[k] += 1
                    if wk in keep and gsis[pid] in agg:
                        s = agg[gsis[pid]].setdefault(wk, {})
                        s[k] = s.get(k, 0) + 1
    except Exception as e:
        print("pbp nicht verfuegbar:", e)

    by_name = {}
    for (n, t) in agg:
        by_name.setdefault(n, []).append(t)
    return agg, by_name, pos_of, season, keep


def build_bio(players_csv):
    bio, by_name = {}, {}
    for r in players_csv:
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
        return round((NOW.replace(tzinfo=None) - datetime.strptime(birth, "%Y-%m-%d")).days / 365.25, 1)
    except (TypeError, ValueError):
        return None


TREND_KEYS = ["off_pct", "def_pct", "st_pct", "targets", "target_share", "wopr", "carries", "opps", "rz_opps",
              "def_tackles_solo", "def_tackle_assists", "def_sacks", "def_pass_defended", "qbh", "tfl"]


def trends(weekly, weeks):
    for w in weeks:                                   # Opportunities = Carries + Targets
        wk = weekly.get(w)
        if wk and ("carries" in wk or "targets" in wk):
            wk["opps"] = (wk.get("carries") or 0) + (wk.get("targets") or 0)
    out = {}
    for k in TREND_KEYS:
        series = [weekly.get(w, {}).get(k) for w in weeks]
        if any(v for v in series):                    # nur Null/None -> weglassen
            out[k] = series
    return out


def def_profile(sd, pos):
    """Saison-Profil Defense: Tackles/Snap, Lauf-Anteil, Tackle-Tiefe, Rolle (nur S, ab 10 Tackles)."""
    if not sd or not sd["def_snaps"]:
        return None
    d = sorted(sd["depth"])
    med = d[len(d) // 2] if d else None
    run_share = sd["run"] / sd["tk"] if sd["tk"] else None
    role = None
    if pos == "S" and sd["tk"] >= 10 and med is not None:
        role = "Box" if med <= 6 and (run_share or 0) >= 0.45 else "Deep" if med >= 9 else "Hybrid"
    return {"snaps": sd["def_snaps"], "tk": sd["tk"], "tps": round(sd["tk"] / sd["def_snaps"], 3),
            "run_share": round(run_share, 2) if run_share is not None else None, "depth": med,
            "qbh": sd["qbh"], "tfl": sd["tfl"],
            "press": round((sd["qbh"] + sd["tfl"]) / sd["def_snaps"], 3), "role": role}


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
    prior = vals[:-1]
    return vals[-1], (vals[-1] - sum(prior) / len(prior)) if prior else None


def pct_of(v, ref):
    """Perzentil von v gegen Referenzliste (FA-Pool). None -> 0."""
    if v is None or not ref:
        return 0.0
    return min(1.0, sum(1 for x in ref if x < v) / max(len(ref) - 1, 1))


def metrics(p):
    g = SCORE_GROUP.get(p["pos"])
    p["group"] = g
    tr = p.get("trend") or {}
    dp = p.get("defense") or {}
    is_idp = g in ("DL", "LB", "S")
    snap_last, snap_delta = last_and_prior(tr.get("def_pct" if is_idp else "off_pct"))
    ts_last, _ = last_and_prior(tr.get("target_share"))
    opps_last, _ = last_and_prior(tr.get("opps"))
    rz_vals = [v for v in (tr.get("rz_opps") or []) if v is not None]
    role = snap_last
    if g in ("WR", "TE") and ts_last is not None:
        role = 0.5 * (snap_last or 0) + 0.5 * min(ts_last * 3, 1)          # Target Share 33 % = voll
    elif g == "RB":                                                    # Touches zaehlen, Snaps allein (FB) kaum
        role = 0.2 * (snap_last or 0) + 0.8 * min((opps_last or 0) / 18, 1)
    elif g in ("LB", "S"):                                             # Snaps + Tackle-Effizienz
        role = 0.5 * (snap_last or 0) + 0.5 * min((dp.get("tps") or 0) / 0.15, 1)
    elif g == "DL":
        role = 0.4 * (snap_last or 0) + 0.3 * min((dp.get("tps") or 0) / 0.10, 1) + 0.3 * min((dp.get("press") or 0) / 0.08, 1)
    p["m"] = {"form1": p.get("pts_last1"), "form3": p.get("pts_last3"), "avg": p.get("pts_avg"),
              "role": role, "role_trend": snap_delta, "own": p.get("pct_owned"), "proj": p.get("proj"),
              "rz": (sum(rz_vals[-3:]) or None) if g in ("RB", "WR", "TE") and rz_vals else None,
              "snap_last": snap_last, "ts_last": ts_last, "opps_last": opps_last,
              "rz_last": rz_vals[-1] if rz_vals else None}

    flags = []
    if snap_delta is not None and snap_delta >= 0.20 and (snap_last or 0) >= 0.50:
        flags.append("SNAP_JUMP")
    if g in ("WR", "TE") and ts_last is not None and ts_last >= 0.20:
        flags.append("TARGETS")
    if g == "RB" and (opps_last or 0) >= 12:
        flags.append("WORKLOAD")
    if g in ("RB", "WR", "TE") and (p["m"]["rz_last"] or 0) >= 3:
        flags.append("RED_ZONE")
    if (g in ("LB", "S") and (snap_last or 0) >= 0.90 and (dp.get("tps") or 0) >= 0.07) or \
            (g == "DL" and (snap_last or 0) >= 0.80):
        flags.append("EVERY_DOWN")
    if g == "DL" and (dp.get("qbh", 0) + dp.get("tfl", 0)) >= 5 and (dp.get("press") or 0) >= 0.05:
        flags.append("PASS_RUSH")
    n = p.get("news") or {}
    if n.get("time") and NOW.timestamp() * 1000 - float(n["time"]) <= 7 * 86400000 and \
            NEWS_POS.search(f'{n.get("text") or ""} {n.get("analysis") or ""}'):
        flags.append("NEWS")
    if p.get("age") is not None and p["age"] <= 24.5 and (snap_delta or 0) >= 0.10 and (snap_last or 0) >= 0.40:
        flags.append("YOUNG_RISER")
    if p.get("injury") in BAD_INJ:
        flags.append("INJURED")
    p["flags"] = flags


def score_all(fas, mine):
    for p in fas + mine:
        metrics(p)
    for g in HOT_N:
        pool = [p for p in fas if p["group"] == g]
        ref = {k: [p["m"][k] for p in pool if p["m"][k] is not None] for k in WEIGHTS}
        for p in [p for p in fas + mine if p["group"] == g]:
            sc = 100 * sum(w * pct_of(p["m"][k], ref[k]) for k, w in WEIGHTS.items())
            if g == "RB":                            # wenig Touches (FB, Garbage-Time) -> nach hinten
                o = [v for v in ((p.get("trend") or {}).get("opps") or []) if v is not None]
                sc *= min(1.0, 0.5 + (sum(o) / len(o) if o else 0) / 10)
            p["score"] = round(sc, 1)
        pool.sort(key=lambda p: -p["score"])
        for i, p in enumerate(pool):
            p["group_rank"] = i + 1
            trig = [f for f in p["flags"] if f != "INJURED"]
            rising = (p["m"]["role_trend"] or 0) >= 0.10 and (p["m"]["snap_last"] or 0) >= 0.40
            if i < HOT_N[g] and p["score"] >= 70:
                p["tier"] = "hot"
            elif trig and p["score"] >= 45:
                p["tier"] = "signal"
            elif p["score"] >= 65 or trig or rising:  # schwache Signale: nicht verlieren, aber nur Radar
                p["tier"] = "radar"
            else:
                p["tier"] = "rest"
    for p in fas + mine:
        if not p["group"]:
            p["score"], p["group_rank"] = None, None
            p["tier"] = "unscored"
        if p["mine"]:
            p["tier"], p["group_rank"] = "mine", None


# ---------- Main ----------
def main():
    equiv = {}
    if os.path.exists("equivalents.json"):
        equiv = {k: v for k, v in json.load(open("equivalents.json")).items() if not k.startswith("_")}
    fas = fetch_free_agents()
    try:
        mine = fetch_my_roster()
    except Exception as e:
        print("Kader nicht verfuegbar:", e)
        mine = []
    players_csv = read_csv("players/players.csv")
    agg, by_name, pos_of, season, weeks = build_nflverse(players_csv)
    bio, bio_by_name = build_bio(players_csv)
    unmatched, review = [], []
    for p in fas + mine:
        n = norm(equiv.get(p["name"], p["name"]))
        t = TEAM_MAP.get(p["team"], p["team"])
        key = None
        if (n, t) in agg:
            key = (n, t)
        elif len(by_name.get(n, [])) == 1:                     # Teamwechsel/FA: Name eindeutig
            cand = (n, by_name[n][0])
            if same_group(p["pos"] or "", pos_of.get(cand, "")):
                key = cand
                review.append(f'{p["name"]} ({p["pos"]}, {p["team"]}) -> {cand[0]} ({pos_of.get(cand)}, {cand[1]})')
        p["trend"] = trends(agg[key], weeks) if key else None
        p["defense"] = def_profile(season.get(key), p["pos"]) if key and p["pos"] in SCORE_GROUP and \
            SCORE_GROUP[p["pos"]] in ("DL", "LB", "S") else None
        if not key and (p.get("pts_total") or 0) > 0:
            unmatched.append(f'{p["name"]} ({p["pos"]}, {p["team"]})')
        b = bio.get((n, t))
        if not b and len(bio_by_name.get(n, [])) == 1:
            cand = bio[bio_by_name[n][0]]
            if same_group(p["pos"] or "", cand.get("pos") or ""):
                b = cand
        p["age"] = age(b["birth"]) if b else None
        p["draft"] = f'R{b["draft_round"]}/{b["draft_pick"]}' if b and b.get("draft_round") else ("UDFA" if b else None)
        p["exp"] = int(b["exp"]) if b and b.get("exp") not in (None, "") else None
        p["rookie"] = bool(b and b.get("rookie"))
    score_all(fas, mine)
    os.makedirs("data", exist_ok=True)
    result = {"generated_utc": NOW.strftime("%Y-%m-%d %H:%M"), "season": SEASON, "trend_weeks": weeks,
              "count": len(fas), "roster_count": len(mine),
              "unmatched_with_points": unmatched, "team_change_matches": review,
              "players": fas + mine}
    json.dump(result, open("data/free_agents.json", "w"), ensure_ascii=False, separators=(",", ":"))
    print(f"{len(fas)} FAs + {len(mine)} Kader, Wochen {weeks}, "
          f"{sum(1 for p in fas + mine if p['trend'])} mit Trend, {len(unmatched)} ohne Match")


if __name__ == "__main__":
    main()

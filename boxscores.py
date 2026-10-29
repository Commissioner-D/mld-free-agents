"""MLD Boxscores: alle Wochen der laufenden Saison (Scoreboard + Boxscore je Spiel) von der
Fleaflicker-API. Grundlage fuer Was-waere-wenn-Rechnungen (z.B. "Wie viele Punkte haette Team X
mit Spieler Y gemacht?"). Ausgabe: data/boxscores.json (vereinfacht) + data/boxscores_raw.json.
"""
import json, os, time, urllib.request, urllib.error
from datetime import datetime, timezone

LEAGUE_ID = 294292
UA = {"User-Agent": "mld-fa/1.0"}
FLEA = "https://www.fleaflicker.com/api"
NOW = datetime.now(timezone.utc)
SEASON = NOW.year if NOW.month >= 8 else NOW.year - 1


def api(path, retries=4):
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(f"{FLEA}/{path}", headers=UA), timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if i == retries - 1:
                raise
            time.sleep(20 * (i + 1) if e.code in (403, 429) else 3)
        except urllib.error.URLError:
            if i == retries - 1:
                raise
            time.sleep(3)


def val(x):
    return (x or {}).get("value") if isinstance(x, dict) else x


def side(slot, key):
    s = slot.get(key) or {}
    pp = s.get("proPlayer") or {}
    if not pp:
        return None
    return {"id": pp.get("id"), "name": pp.get("nameFull"), "pos": pp.get("position"),
            "team": pp.get("proTeamAbbreviation"), "pts": val(s.get("viewingActualPoints"))}


def main():
    os.makedirs("data", exist_ok=True)
    weeks, raw = [], {}
    for week in range(1, 19):
        sb = api(f"FetchLeagueScoreboard?sport=NFL&league_id={LEAGUE_ID}&season={SEASON}&scoring_period={week}")
        games = sb.get("games") or []
        if not games or not any(g.get("isFinalScore") or g.get("isInProgress") for g in games):
            break
        wk = {"week": week, "games": []}
        for g in games:
            gid = g["id"]
            bx = api(f"FetchLeagueBoxscore?sport=NFL&league_id={LEAGUE_ID}&fantasy_game_id={gid}&scoring_period={week}")
            raw[f"{week}-{gid}"] = bx
            game = {"id": gid, "final": bool(g.get("isFinalScore")),
                    "away": {"id": g["away"]["id"], "name": g["away"]["name"], "score": val((g.get("awayScore") or {}).get("score"))},
                    "home": {"id": g["home"]["id"], "name": g["home"]["name"], "score": val((g.get("homeScore") or {}).get("score"))},
                    "lineups": {"away": [], "home": []}}
            for grp in bx.get("lineups") or []:
                for sl in grp.get("slots") or []:
                    label = (sl.get("position") or {}).get("label")
                    for k in ("away", "home"):
                        p = side(sl, k)
                        if p:
                            game["lineups"][k].append({"group": grp.get("group"), "slot": label, **p})
            wk["games"].append(game)
            time.sleep(0.5)
        weeks.append(wk)
        print(f"Woche {week}: {len(games)} Spiele")
    out = {"generated_utc": NOW.strftime("%Y-%m-%d %H:%M"), "season": SEASON, "weeks": weeks}
    json.dump(out, open("data/boxscores.json", "w"), ensure_ascii=False)
    json.dump(raw, open("data/boxscores_raw.json", "w"), ensure_ascii=False)


if __name__ == "__main__":
    main()

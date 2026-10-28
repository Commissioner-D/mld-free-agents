"""MLD Liga-Snapshot: Standings, alle 16 Kader, Picks, letzte Trades und Transaktionen.

Wird von fetch.py aufgerufen (nutzt dessen Helfer, Bio-Daten und Scores).
Ausgabe: data/league.json
"""
import html as _html, json, re, time
from datetime import datetime, timezone

TRADE_PAGES = 3          # je 10 Trades
TX_PAGES = 4             # je 30 Transaktionen
PAUSE = 1.0            # Fleaflicker drosselt bei schnellen Serienabrufen
TX_TYPES = {None: "ADD", "TRANSACTION_ADD": "ADD", "TRANSACTION_DROP": "DROP",
            "TRANSACTION_CLAIM": "CLAIM", "TRANSACTION_TRADE": "TRADE"}


def _date(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _pro(pp):
    return {"fl_id": pp.get("id"), "name": pp.get("nameFull"), "pos": pp.get("position"),
            "team": pp.get("proTeamAbbreviation")}


def _pick(pk, team_id=None):
    orig = pk.get("originalOwner") or {}
    return {"season": pk.get("season"), "round": (pk.get("slot") or {}).get("round"),
            "from": orig.get("name") if orig.get("id") not in (None, team_id) else None}


def parse_leaders(page):
    """Leaders-Seite (HTML): Team -> (PF, Optimum PF). Die API liefert PfO nicht."""
    i = page.find("<table")
    table = page[i:page.find("</table>", i)]
    cell = lambda c: _html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
    heads = [cell(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", table, re.S)]
    if "Team" not in heads or "Optimum PF" not in heads:
        raise ValueError("Leaders-Tabelle unerwartet: " + ",".join(heads))
    it, io = heads.index("Team"), heads.index("Optimum PF")
    num = lambda x: float(re.sub(r"[^\d.]", "", x.split("(")[0].replace(",", "")) or 0)
    out = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S):
        cells = [cell(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) > io:
            out[cells[it]] = num(cells[io])
    return out


def build(api, league_id, my_team_id, parse_player, bio_for, scored, page=None):
    """api(path) -> dict; parse_player wie in fetch.py; bio_for(p) -> dict|None;
    scored: {fl_id: Spieler aus dem Liga-Pool mit score/tier/flags/m}."""
    st = api(f"FetchLeagueStandings?sport=NFL&league_id={league_id}")
    teams = []
    for div in st.get("divisions", []):
        for t in div.get("teams", []):
            rec = t.get("recordOverall") or {}
            teams.append({
                "id": t["id"], "name": t["name"], "mine": t["id"] == my_team_id,
                "owner": ", ".join(o.get("displayName", "") for o in t.get("owners", [])),
                "division": div.get("name"), "record": rec.get("formatted"),
                "wins": rec.get("wins", 0), "losses": rec.get("losses", 0), "ties": rec.get("ties", 0),
                "rank": rec.get("rank"), "pf": (t.get("pointsFor") or {}).get("value"),
                "pa": (t.get("pointsAgainst") or {}).get("value"),
                "streak": (t.get("streak") or {}).get("formatted"),
                "faab": (t.get("waiverAcquisitionBudget") or {}).get("value"),
            })

    for t in teams:
        r = api(f"FetchRoster?sport=NFL&league_id={league_id}&team_id={t['id']}")
        roster = []
        for g in r.get("groups", []):
            slot = {"START": "Start", "INJURED": "IR", "TAXI": "Taxi"}.get(g.get("group"), "Bank")
            for s in g.get("slots", []):
                lp = s.get("leaguePlayer")
                if not (lp and lp.get("proPlayer")):
                    continue
                p = parse_player(lp, slot)
                b = bio_for(p) or {}
                sc = scored.get(p["fl_id"]) or {}
                m = sc.get("m") or {}
                roster.append({
                    "fl_id": p["fl_id"], "name": p["name"], "pos": p["pos"], "team": p["team"], "slot": slot,
                    "injury": p["injury"], "age": b.get("age"), "draft": b.get("draft"), "exp": b.get("exp"),
                    "rookie": b.get("rookie"), "pts_avg": p["pts_avg"], "pts_last3": p["pts_last3"],
                    "pts_total": p["pts_total"], "pos_rank": p["pos_rank"], "pct_owned": p["pct_owned"],
                    "score": sc.get("score"), "group": sc.get("group"), "flags": sc.get("flags") or [],
                    "snap_last": m.get("snap_last"), "role_trend": m.get("role_trend"),
                })
        t["roster"] = roster
        time.sleep(PAUSE)
        pk = api(f"FetchTeamPicks?sport=NFL&league_id={league_id}&team_id={t['id']}")
        t["picks"] = sorted((_pick(x, t["id"]) for x in pk.get("picks", []) if not x.get("lost")),
                            key=lambda x: (x["season"] or 0, x["round"] or 0))
        time.sleep(PAUSE)

    def trade_rec(tr):
        return {
                "id": tr.get("id"), "status": tr.get("status"), "date": _date(tr.get("approvedOn") or tr.get("proposedOn")),
                "description": tr.get("description"),
                "teams": [{"team": (x.get("team") or {}).get("name"),
                           "players": [_pro(p["proPlayer"]) for p in x.get("playersObtained", []) if p.get("proPlayer")],
                           "picks": [_pick(p) for p in x.get("picksObtained", [])]}
                          for x in tr.get("teams", [])]}

    trades, offset = [], 0
    for _ in range(TRADE_PAGES):
        d = api(f"FetchTrades?sport=NFL&league_id={league_id}&filter=TRADES_COMPLETED&result_offset={offset}")
        trades += [trade_rec(tr) for tr in d.get("trades", [])]
        nxt = d.get("resultOffsetNext")
        if not nxt or nxt <= offset:
            break
        offset = nxt
        time.sleep(PAUSE)

    d = api(f"FetchTrades?sport=NFL&league_id={league_id}&filter=TRADES_UNDER_REVIEW")
    pending = [trade_rec(tr) for tr in d.get("trades", [])]

    tx, offset = [], 0
    for _ in range(TX_PAGES):
        d = api(f"FetchLeagueTransactions?sport=NFL&league_id={league_id}&result_offset={offset}")
        for it in d.get("items", []):
            x = it.get("transaction") or {}
            pp = (x.get("player") or {}).get("proPlayer") or {}
            tx.append({"date": _date(it.get("timeEpochMilli")), "type": TX_TYPES.get(x.get("type"), x.get("type")),
                       "team": (x.get("team") or {}).get("name"), "fl_id": pp.get("id"),
                       "name": pp.get("nameFull"), "pos": pp.get("position"),
                       "nfl": pp.get("proTeamAbbreviation"), "bid": x.get("bidAmount")})
        nxt = d.get("resultOffsetNext")
        if not nxt or nxt <= offset:
            break
        offset = nxt
        time.sleep(PAUSE)

    # Points for Optimum: bestimmt die Draft-Reihenfolge (niedrigster PfO = frueher Pick)
    if page:
        try:
            pfo = parse_leaders(page(f"https://www.fleaflicker.com/nfl/leagues/{league_id}/leaders"))
            for t in teams:
                t["pfo"] = pfo.get(t["name"])
            ranked = sorted([t for t in teams if t.get("pfo") is not None], key=lambda t: t["pfo"])
            for i, t in enumerate(ranked):
                t["draft_slot"] = i + 1
        except Exception as e:
            print("PfO nicht verfuegbar:", e)

    teams.sort(key=lambda t: (t["rank"] or 99))
    return {"teams": teams, "trades_pending": pending, "trades": trades, "transactions": tx}

"""Automatische Spieler-Recherche via Claude API (Websuche) -> scouting.json.

Laeuft woechentlich vor den Waivers (und manuell). Recherchiert die aktuellen Heiss-/Signal-Free-Agents,
deren Eintrag fehlt oder aelter als REFRESH_DAYS ist. Manuelle Eintraege (ohne "auto": true) werden
nur ersetzt, wenn sie veraltet sind. Ohne Secret ANTHROPIC_API_KEY passiert nichts.
"""
import json, os, re, sys, urllib.request
from datetime import datetime, timezone, timedelta

API_KEY = os.environ.get("ANTHROPIC_API_KEY")
MODEL = os.environ.get("RESEARCH_MODEL", "claude-sonnet-5-5")
MAX_PLAYERS = int(os.environ.get("RESEARCH_MAX", "12"))     # Kostenbremse pro Lauf
REFRESH_DAYS = 7
DATA_URL = "https://raw.githubusercontent.com/Commissioner-D/mld-free-agents/data/data/free_agents.json"
TODAY = datetime.now(timezone.utc).date()
ROLES = {"S": ["Box", "Hybrid", "Deep"], "LB": ["Every Down", "Early Down", "Rotation", "Pass Rush"]}
BADGES = ["Sicherer Starter", "Interim", "Rolle unsicher", "Aufsteiger", "Must Add"]


def age_days(entry):
    try:
        return (TODAY - datetime.strptime(entry.get("date", ""), "%Y-%m-%d").date()).days
    except ValueError:
        return 999


def pick(players, scouting):
    cands = [p for p in players if not p.get("mine") and p.get("tier") in ("hot", "signal")]
    cands.sort(key=lambda p: (p["tier"] != "hot", -(p.get("score") or 0)))
    out = []
    for p in cands:
        e = scouting.get(p["name"])
        if e is None or age_days(e) > REFRESH_DAYS:
            out.append(p)
        if len(out) >= MAX_PLAYERS:
            break
    return out


def ask(p):
    roles = ROLES.get(p["pos"])
    prompt = f"""Heute ist {TODAY}. Recherchiere per Websuche die aktuelle Situation von {p['name']} ({p['pos']}, NFL-Team {p['team']}) fuer eine Dynasty-Fantasy-Liga mit IDP.
Fokus: Ist er Starter? Stabil oder nur Ersatz fuer verletzte Spieler (wen, und wann kommen die zurueck)? Rolle im Scheme? Trend der letzten Wochen?
Nutze nur Quellen aus den letzten 3 Wochen, wo moeglich (Beat-Writer, CBS, Rotowire, Pro Football Rumors, Team-Seiten).

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt, ohne Text davor oder danach:
{{"role": {json.dumps(roles) + ' oder null' if roles else 'null'},
 "badge": eines von {json.dumps(BADGES)} oder null,
 "interim_for": "Name(n) der vertretenen Spieler oder null",
 "until": "kurz, z.B. 'bis W5+' oder null",
 "until_detail": "ein Satz zum Rueckkehr-Zeitplan oder null",
 "note": "2-3 Saetze auf Deutsch mit korrekten Umlauten, max. 350 Zeichen",
 "source": "Medien, kommagetrennt"}}
Setze role nur, wenn die Quellen die Rolle klar belegen. Keine Vermutungen als Fakten."""
    body = {"model": MODEL, "max_tokens": 1500, "messages": [{"role": "user", "content": prompt}],
            "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]}
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                 headers={"x-api-key": API_KEY, "anthropic-version": "2023-06-01",
                                          "content-type": "application/json"})
    d = json.loads(urllib.request.urlopen(req, timeout=180).read())
    text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("kein JSON in Antwort")
    r = json.loads(m.group(0))
    if roles is None or r.get("role") not in roles:
        r["role"] = None
    if r.get("badge") not in BADGES:
        r["badge"] = None
    r = {k: v for k, v in r.items() if v not in (None, "", "null")}
    r.update({"date": str(TODAY), "auto": True})
    return r


def main():
    if not API_KEY:
        print("Kein ANTHROPIC_API_KEY gesetzt -> keine Recherche.")
        return
    scouting = json.load(open("scouting.json")) if os.path.exists("scouting.json") else {}
    players = json.loads(urllib.request.urlopen(urllib.request.Request(DATA_URL, headers={"User-Agent": "mld-fa/1.0"})).read())["players"]
    todo = pick(players, scouting)
    print(f"Recherchiere {len(todo)} Spieler: {[p['name'] for p in todo]}")
    done = 0
    for p in todo:
        try:
            scouting[p["name"]] = ask(p)
            done += 1
            print("ok", p["name"], scouting[p["name"]].get("badge"), scouting[p["name"]].get("role"))
        except Exception as e:
            print("Fehler", p["name"], e, file=sys.stderr)
    if done:
        json.dump(scouting, open("scouting.json", "w"), indent=2, ensure_ascii=False)
        open("scouting.json", "a").write("\n")


if __name__ == "__main__":
    main()

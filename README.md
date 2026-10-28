# mld-free-agents

Free-Agent-Radar und Liga-Snapshot für die Major League Dynasty (Fleaflicker 294292).

Seite: https://commissioner-d.github.io/mld-free-agents

## Daten (Branch `data`, alle 4 h überschrieben)

- `data/free_agents.json`: alle FAs + eigener Kader mit Score, Tier, Flags, Trends, Depth Chart, News
- `data/league.json`: Standings, alle 16 Kader (inkl. Alter, Score, Flags), Draft-Picks pro Team,
  letzte 30 Trades, letzte 120 Transaktionen

Raw-URLs:
- https://raw.githubusercontent.com/Commissioner-D/mld-free-agents/data/data/free_agents.json
- https://raw.githubusercontent.com/Commissioner-D/mld-free-agents/data/data/league.json

## Workflows

- **Fetch Free Agents**: alle 4 h, manuell, und bei Änderungen an `fetch.py` / `league.py`
- **Research**: Di 17:00 UTC, braucht das Secret `ANTHROPIC_API_KEY`

Picks: Fleaflicker kennt für künftige Drafts noch keine Reihenfolge, daher nur Saison, Runde und Herkunft.

## Wird genutzt von

- **mld-copilot** liest `data/league.json` (Kader-Infos, `pfo`, `draft_slot`) für Spielerinfos und Pick-Tendenzen.
  Feldnamen dort nicht umbenennen, ohne den Copilot anzupassen.

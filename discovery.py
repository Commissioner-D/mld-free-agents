import json, urllib.request, urllib.error, os
os.makedirs("discovery", exist_ok=True)
B = "https://www.fleaflicker.com/api"
tests = {
  "standings_mlfua": ("FetchLeagueStandings?sport=NFL&league_id=294292", "mlf-stats/1.0"),
  "fa_mlfua": ("FetchPlayerListing?sport=NFL&league_id=294292&filter.free_agent_only=true&sort=SORT_PROJECTED", "mlf-stats/1.0"),
  "fa_customua": ("FetchPlayerListing?sport=NFL&league_id=294292&filter.free_agent_only=true", "mld-fa/1.0"),
  "fa_lb": ("FetchPlayerListing?sport=NFL&league_id=294292&filter.free_agent_only=true&filter.position.eligibility=LB", "mld-fa/1.0"),
  "fa_off30": ("FetchPlayerListing?sport=NFL&league_id=294292&filter.free_agent_only=true&result_offset=30", "mld-fa/1.0"),
  "listing_nofilter": ("FetchPlayerListing?sport=NFL&league_id=294292", "mld-fa/1.0"),
}
for name, (ep, ua) in tests.items():
    try:
        r = urllib.request.urlopen(urllib.request.Request(f"{B}/{ep}", headers={"User-Agent": ua}), timeout=30)
        body = r.read(); code = r.status
    except urllib.error.HTTPError as e:
        body = e.read(); code = e.code
    open(f"discovery/{name}.json", "wb").write(body)
    print(name, code, len(body))

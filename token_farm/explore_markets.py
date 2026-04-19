"""Explore Entain SGM pricing across points, assists, rebounds + cross-market combos.
Shows how correlation penalty varies by leg type."""
import asyncio, sys, re
from itertools import combinations
from entain_client import EntainClient

EVENT_ID = "27b7d5e0-55c2-445e-9cdb-bb013d9d9e01"  # Cavs vs Raptors

# Market name patterns we care about
MARKET_CATEGORIES = {
    "points": re.compile(r"^To Score (\d+)\+ Points$", re.I),
    "assists": re.compile(r"^To Have (\d+)\+ Assists$", re.I),
    "rebounds": re.compile(r"^To Have (\d+)\+ Rebounds$", re.I),
    "threes": re.compile(r"^To Make (\d+)\+ Three Point FG$", re.I),
}


def fmt(v):
    return f"{v:.2f}" if v else "  - "


async def main():
    c = EntainClient(brand="neds")
    await c.login(sys.argv[1], sys.argv[2])
    card = await c.get_event_card(EVENT_ID)
    entrants, prices, markets = card["entrants"], card["prices"], card["markets"]

    # Collect markets by category
    cat_markets: dict[str, list] = {k: [] for k in MARKET_CATEGORIES}
    for mid, m in markets.items():
        name = m.get("name", "")
        for cat, pat in MARKET_CATEGORIES.items():
            mm = pat.match(name)
            if mm:
                cat_markets[cat].append({"id": mid, "name": name, "threshold": int(mm.group(1)),
                                          "entrants": m.get("entrant_ids", [])})

    print("=== MARKETS FOUND ===")
    for cat, mks in cat_markets.items():
        mks.sort(key=lambda m: m["threshold"])
        print(f"  {cat}: {[m['threshold'] for m in mks]}")

    # Single-leg odds lookup
    def single_dec(entrant_id):
        for k, v in prices.items():
            if k.startswith(entrant_id) and isinstance(v, dict):
                o = v.get("odds", {})
                if o.get("numerator", 0) > 0 and o.get("denominator", 0) > 0:
                    return 1 + o["numerator"] / o["denominator"]
        return None

    # Focus on a small set of key Cavs + Raptors players
    KEYS = ["Donovan Mitchell", "Evan Mobley", "Darius Garland", "Jarrett Allen",
            "Scottie Barnes", "R.J. Barrett", "Immanuel Quickley", "Jakob Poeltl"]
    # Player table: rows = players, cols = thresholds
    print("\n=== POINTS TABLE ===  (single leg decimal odds)")
    pts_mks = cat_markets["points"]
    header = "Player".ljust(32) + "".join(f"{m['threshold']}+ pts".rjust(10) for m in pts_mks)
    print(header)
    for player in KEYS:
        row = player.ljust(32)
        for m in pts_mks:
            found = None
            for eid in m["entrants"]:
                n = entrants.get(eid, {}).get("name", "")
                if player in n:
                    found = single_dec(eid); break
            row += fmt(found).rjust(10)
        print(row)

    print("\n=== ASSISTS TABLE ===")
    ast_mks = cat_markets["assists"]
    header = "Player".ljust(32) + "".join(f"{m['threshold']}+ ast".rjust(10) for m in ast_mks)
    print(header)
    for player in KEYS:
        row = player.ljust(32)
        for m in ast_mks:
            found = None
            for eid in m["entrants"]:
                n = entrants.get(eid, {}).get("name", "")
                if player in n:
                    found = single_dec(eid); break
            row += fmt(found).rjust(10)
        print(row)

    print("\n=== REBOUNDS TABLE ===")
    reb_mks = cat_markets["rebounds"]
    header = "Player".ljust(32) + "".join(f"{m['threshold']}+ reb".rjust(10) for m in reb_mks)
    print(header)
    for player in KEYS:
        row = player.ljust(32)
        for m in reb_mks:
            found = None
            for eid in m["entrants"]:
                n = entrants.get(eid, {}).get("name", "")
                if player in n:
                    found = single_dec(eid); break
            row += fmt(found).rjust(10)
        print(row)

    # Helper to find entrant+market for a given player + category + threshold
    def find(player, cat, threshold):
        for m in cat_markets[cat]:
            if m["threshold"] != threshold: continue
            for eid in m["entrants"]:
                n = entrants.get(eid, {}).get("name", "")
                if player in n:
                    return m["id"], eid, n
        return None, None, None

    # SGM combinations to explore
    print("\n=== SGM CORRELATION TESTS ===")
    combos = [
        # Same player, different markets (expect HIGH correlation penalty)
        ("Mobley 10+ pts + Mobley 5+ reb",  [("Evan Mobley", "points", 10), ("Evan Mobley", "rebounds", 5)]),
        ("Mobley 15+ pts + Mobley 6+ reb",  [("Evan Mobley", "points", 15), ("Evan Mobley", "rebounds", 6)]),
        ("Mobley 20+ pts + Mobley 5+ reb",  [("Evan Mobley", "points", 20), ("Evan Mobley", "rebounds", 5)]),
        ("Mitchell 20+ pts + Mitchell 3+ ast", [("Donovan Mitchell", "points", 20), ("Donovan Mitchell", "assists", 3)]),
        ("Garland 15+ pts + Garland 4+ ast", [("Darius Garland", "points", 15), ("Darius Garland", "assists", 4)]),
        # Different players same market (teammate scoring — anti-correlated if bench)
        ("Mobley 15+ + Allen 15+",          [("Evan Mobley", "points", 15), ("Jarrett Allen", "points", 15)]),
        ("Mitchell 20+ + Mobley 15+",       [("Donovan Mitchell", "points", 20), ("Evan Mobley", "points", 15)]),
        # Cross-player cross-market
        ("Mitchell 25+ pts + Garland 4+ ast", [("Donovan Mitchell", "points", 25), ("Darius Garland", "assists", 4)]),
        ("Mobley 6+ reb + Allen 6+ reb",    [("Evan Mobley", "rebounds", 6), ("Jarrett Allen", "rebounds", 6)]),
        # Opposing teams
        ("Mitchell 20+ + Barnes 10+",       [("Donovan Mitchell", "points", 20), ("Scottie Barnes", "points", 10)]),
    ]

    print(f"{'combo':<42} {'leg1':>7} {'leg2':>7} {'raw':>7} {'SGM':>7}  effect")
    print("-" * 90)
    for label, legs in combos:
        try:
            resolved = []
            dec_prod = 1.0
            ok = True
            leg_decs = []
            for player, cat, thresh in legs:
                mid, eid, name = find(player, cat, thresh)
                if not mid:
                    print(f"{label:<42}  (market/player not found: {player} {cat} {thresh})")
                    ok = False; break
                dec = single_dec(eid)
                if dec is None:
                    print(f"{label:<42}  (no price for {name})")
                    ok = False; break
                dec_prod *= dec
                leg_decs.append(dec)
                resolved.append({"market_id": mid, "entrant_id": eid})
            if not ok: continue

            quote = await c.get_sgm_quote(EVENT_ID, resolved)
            p = quote.get("prices", {}).get(EVENT_ID, {})
            if not p.get("available"):
                print(f"{label:<42} {leg_decs[0]:>7.2f} {leg_decs[1]:>7.2f} {dec_prod:>7.2f}  SGM not available")
                continue
            o = p["odds"]
            sgm = 1 + o["numerator"] / o["denominator"]
            gap = (dec_prod - sgm) / dec_prod * 100
            if gap > 2:
                effect = f"↓{gap:.1f}% penalty (correlated)"
            elif gap < -2:
                effect = f"↑{-gap:.1f}% BOOST (anti-correlated)"
            else:
                effect = "≈ independent"
            print(f"{label:<42} {leg_decs[0]:>7.2f} {leg_decs[1]:>7.2f} {dec_prod:>7.2f} {sgm:>7.2f}  {effect}")
        except Exception as e:
            print(f"{label:<42}  EXC: {e!r}")

    await c.close()


asyncio.run(main())

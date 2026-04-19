"""Lightning-fast SGM scanner: every 2-leg combo across all player markets.

Usage:   python3 scan_sgm.py <event_id> [--concurrency N] [--min-odds X] [--max-odds Y]

Fetches event-card once, enumerates all 2-leg pairs meeting filters, then quotes
them via concurrent HTTP (default 25 parallel). On a 10-player NBA game with
~150 player props, full scan completes in ~30-60s.

Reports top boosts (anti-correlated, SGM > raw), top penalties (correlated),
and highest-value combos by implied profit.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from itertools import combinations
from pathlib import Path

import httpx
from entain_client import EntainClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("scan")
# Silence httpx per-request logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


PLAYER_MARKETS_RE = re.compile(
    r"^(To Score \d+\+ Points|"
    r"To Have \d+\+ Assists|"
    r"To Have \d+\+ Rebounds|"
    r"To Make \d+\+ Three Point FG)$",
    re.IGNORECASE,
)


def single_dec_for(entrant_id, prices):
    for k, v in prices.items():
        if k.startswith(entrant_id) and isinstance(v, dict):
            o = v.get("odds", {})
            if o.get("numerator", 0) > 0 and o.get("denominator", 0) > 0:
                return 1 + o["numerator"] / o["denominator"]
    return None


async def quote_one(sem, client, url):
    async with sem:
        try:
            r = await client.get(url)
            if r.status_code != 200:
                return None
            return r.json()
        except Exception as e:
            return None


async def scan(c: EntainClient, event_id: str, min_odds: float, max_odds: float,
               concurrency: int, include_same_player: bool):
    log.info(f"fetching event-card for {event_id[:8]}...")
    card = await c.get_event_card(event_id)
    entrants = card["entrants"]
    prices = card["prices"]
    markets = card["markets"]

    # Build list of (player_name, market_id, market_name, entrant_id, decimal_odds)
    props = []
    for mid, m in markets.items():
        name = m.get("name", "")
        if not PLAYER_MARKETS_RE.match(name):
            continue
        for eid in m.get("entrant_ids", []):
            ent = entrants.get(eid, {})
            if not ent.get("visible", True):
                continue
            pname = ent.get("name", "")
            dec = single_dec_for(eid, prices)
            if dec is None or dec < min_odds or dec > max_odds:
                continue
            props.append({"player": pname, "market_id": mid, "market": name,
                          "entrant_id": eid, "dec": dec})
    log.info(f"  {len(props)} valid props (after {min_odds} <= odds <= {max_odds})")

    # Generate 2-leg pairs; skip same (player, market) dupes + optionally same-player
    pairs = []
    for a, b in combinations(range(len(props)), 2):
        pa, pb = props[a], props[b]
        if pa["market_id"] == pb["market_id"] and pa["entrant_id"] == pb["entrant_id"]:
            continue
        if not include_same_player and pa["player"] == pb["player"]:
            continue
        pairs.append((pa, pb))
    log.info(f"  {len(pairs)} candidate pairs to quote")

    # Concurrent quotes
    sem = asyncio.Semaphore(concurrency)
    http = c._client()
    from urllib.parse import quote as uq

    def make_url(pa, pb):
        body = {event_id: {"event_id": event_id,
                            "selections": [
                                {"market_id": pa["market_id"], "entrant_id": pa["entrant_id"]},
                                {"market_id": pb["market_id"], "entrant_id": pb["entrant_id"]},
                            ]}}
        return f"{c.api_base}/v2/same-game-multi/get-odds?same_game_multies={uq(json.dumps(body))}"

    t0 = time.time()
    # gather() preserves order so pairs zip to results correctly
    tasks = [quote_one(sem, http, make_url(pa, pb)) for pa, pb in pairs]
    results = await asyncio.gather(*tasks)
    log.info(f"  all quotes done in {time.time() - t0:.1f}s")

    # Stitch results with pair inputs
    records = []
    for (pa, pb), q in zip(pairs, results):
        if not q: continue
        p = q.get("prices", {}).get(event_id, {})
        if not p.get("available"):
            continue
        o = p["odds"]
        sgm = 1 + o["numerator"] / o["denominator"]
        raw = pa["dec"] * pb["dec"]
        gap_pct = (raw - sgm) / raw * 100
        records.append({
            "p1": pa["player"], "m1": pa["market"], "dec1": pa["dec"],
            "p2": pb["player"], "m2": pb["market"], "dec2": pb["dec"],
            "raw": raw, "sgm": sgm, "gap_pct": gap_pct,
            "odds": o, "market_id1": pa["market_id"], "entrant_id1": pa["entrant_id"],
            "market_id2": pb["market_id"], "entrant_id2": pb["entrant_id"],
        })

    return records


def fmt_row(r):
    return (f"{r['p1']:<24} {r['m1'][:20]:<20} {r['dec1']:>5.2f}  |  "
            f"{r['p2']:<24} {r['m2'][:20]:<20} {r['dec2']:>5.2f}  |  "
            f"raw {r['raw']:>6.2f}  sgm {r['sgm']:>6.2f}  gap {r['gap_pct']:+5.1f}%")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("event_id")
    ap.add_argument("--concurrency", type=int, default=25)
    ap.add_argument("--min-odds", type=float, default=1.08)
    ap.add_argument("--max-odds", type=float, default=25.0)
    ap.add_argument("--same-player", action="store_true",
                    help="include same-player cross-market combos")
    ap.add_argument("--user", default=os.environ.get("ENTAIN_USER", "williamdean327"))
    ap.add_argument("--pass", dest="password", default=os.environ.get("ENTAIN_PASS", "Deanslister27!"))
    ap.add_argument("--brand", default="neds")
    ap.add_argument("--out", default="/tmp/scan_sgm.json")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    c = EntainClient(brand=args.brand)
    t0 = time.time()
    log.info("login...")
    await c.login(args.user, args.password)
    log.info(f"  login ok ({time.time() - t0:.1f}s)")

    recs = await scan(c, args.event_id,
                      min_odds=args.min_odds, max_odds=args.max_odds,
                      concurrency=args.concurrency,
                      include_same_player=args.same_player)

    await c.close()

    Path(args.out).write_text(json.dumps(recs, indent=2, default=str))
    log.info(f"total scan time: {time.time() - t0:.1f}s, {len(recs)} records -> {args.out}")

    # Top boosts (most anti-correlated — SGM pays more than raw)
    recs.sort(key=lambda r: r["gap_pct"])
    print("\n=== TOP BOOSTS (SGM > raw multi) ===")
    for r in recs[:args.top]:
        print(fmt_row(r))
    # Top penalties
    print("\n=== TOP PENALTIES (SGM < raw multi) ===")
    for r in reversed(recs[-args.top:]):
        print(fmt_row(r))

    # Highest absolute SGM odds
    recs.sort(key=lambda r: r["sgm"], reverse=True)
    print("\n=== HIGHEST SGM ODDS (biggest potential payout) ===")
    for r in recs[:args.top]:
        print(fmt_row(r))


if __name__ == "__main__":
    asyncio.run(main())

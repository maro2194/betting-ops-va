"""Drive EntainClient: login, quote SGM, place $5.

Target: Cleveland Cavaliers vs Toronto Raptors, "To Score 10+ Points" market.
event_id: 27b7d5e0-55c2-445e-9cdb-bb013d9d9e01
market_id (10+ pts): 2d55ebb0-d62e-4d7a-b457-44e83255c96d
"""
import asyncio
import json
import logging
import sys

from entain_client import EntainClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("run")

EVENT_ID = "27b7d5e0-55c2-445e-9cdb-bb013d9d9e01"  # Cavs vs Raptors
MARKET_10PLUS = "2d55ebb0-d62e-4d7a-b457-44e83255c96d"  # To Score 10+ Points

# Players we've NOT already bet on (HAR used Scottie Barnes + R.J. Barrett).
# Pick two Cavs/Raptors entrants visible in event-card.
PREFERRED_PLAYERS = [
    "Donovan Mitchell", "Darius Garland", "Evan Mobley", "Jarrett Allen",
    "Max Strus", "Immanuel Quickley", "Brandon Ingram", "Jakob Poeltl",
]


async def main():
    brand, username, password = sys.argv[1:4]
    stake = float(sys.argv[4]) if len(sys.argv) > 4 else 5.0
    dry_run = "--dry-run" in sys.argv

    c = EntainClient(brand=brand)
    log.info("Logging in...")
    r = await c.login(username, password)
    log.info(f"  login: {r['success']}, {len(r['cookies'])} cookies")

    # Get event card
    log.info(f"Fetching event card for {EVENT_ID[:8]}...")
    card = await c.get_event_card(EVENT_ID)
    entrants = card.get("entrants", {})
    markets = card.get("markets", {})
    log.info(f"  entrants={len(entrants)}  markets={len(markets)}")

    # Find the market object (if present)
    if MARKET_10PLUS in markets:
        m = markets[MARKET_10PLUS]
        log.info(f"  market 10+ pts: name={m.get('name')!r}  entrant_ids={len(m.get('entrant_ids', []))}")
        market_entrant_ids = m.get("entrant_ids", [])
    else:
        log.warning(f"  market {MARKET_10PLUS} not in markets dict — falling back to name search")
        # search markets by name
        market_entrant_ids = []
        for mid, mk in markets.items():
            if "10+" in (mk.get("name") or "").lower() or "to score 10" in (mk.get("name") or "").lower():
                log.info(f"    matched market {mid}: {mk.get('name')}")
                market_entrant_ids = mk.get("entrant_ids", [])
                break

    if not market_entrant_ids:
        # Fallback: just scan entrants whose name contains NBA player words
        log.warning("  scanning entrants by name")
        market_entrant_ids = [eid for eid, e in entrants.items() if "(" in (e.get("name") or "")]
        log.info(f"    candidate count={len(market_entrant_ids)}")

    # Pick 2 preferred players
    selections = []
    picked_names = []
    for eid in market_entrant_ids:
        e = entrants.get(eid)
        if not e: continue
        if not e.get("visible", True): continue
        name = e.get("name", "")
        for pname in PREFERRED_PLAYERS:
            if pname in name and pname not in " ".join(picked_names):
                selections.append({"market_id": MARKET_10PLUS, "entrant_id": eid})
                picked_names.append(name)
                break
        if len(selections) >= 2:
            break

    if len(selections) < 2:
        # Just pick any 2 visible
        log.warning(f"  preferred not found, picking any 2")
        for eid in market_entrant_ids:
            e = entrants.get(eid)
            if not e or not e.get("visible", True): continue
            selections.append({"market_id": MARKET_10PLUS, "entrant_id": eid})
            picked_names.append(e.get("name", ""))
            if len(selections) >= 2:
                break

    log.info(f"Picked {len(selections)} legs:")
    for s, n in zip(selections, picked_names):
        log.info(f"  - {n}  entrant={s['entrant_id']}")

    if len(selections) < 2:
        log.error("couldn't find 2 eligible entrants")
        await c.close()
        sys.exit(1)

    # Quote
    log.info("Quoting SGM...")
    quote = await c.get_sgm_quote(EVENT_ID, selections)
    log.info(f"  quote: {json.dumps(quote)[:400]}")

    price_obj = quote.get("prices", {}).get(EVENT_ID, {})
    if not price_obj.get("available"):
        log.error("SGM not available for this combo")
        await c.close()
        sys.exit(2)

    odds = price_obj.get("odds", {})
    decimal_odds = 1.0 + odds.get("numerator", 0) / max(odds.get("denominator", 1), 1)
    log.info(f"  SGM odds: {odds}  decimal={decimal_odds:.2f}")

    # Need a bet_id per leg — HAR showed each leg has its own bet_id (UUID from quote response?)
    # In the HAR the quote response didn't expose bet_id explicitly; it came from single-bet creation.
    # Let's try placing with the quote response's derived structure.
    # From HAR place-bet payload: bet_id was a UUID — likely server-generated.
    # Try generating UUIDs client-side (sometimes servers accept this) OR omit bet_id.
    import uuid as _u
    legs = []
    for s, n in zip(selections, picked_names):
        legs.append({
            "bet_id": str(_u.uuid4()),  # try client-generated
            "entrant_name": n,
            "odds": {"numerator": 20, "denominator": 100},  # dummy; server may ignore vs quoted_odds
            "market_id": s["market_id"],
            "entrant_id": s["entrant_id"],
        })

    if dry_run:
        log.info(f"DRY RUN — would place $ {stake} @ {decimal_odds:.2f} on {picked_names}")
        await c.close()
        return

    log.info(f"Placing ${stake} SGM...")
    result = await c.place_sgm(
        event_id=EVENT_ID, legs=legs, stake=stake, quoted_odds=odds,
    )
    log.info(f"  HTTP {result['status']}")
    log.info(f"  body: {json.dumps(result['body'])[:800] if isinstance(result['body'], dict) else str(result['body'])[:800]}")

    await c.close()


if __name__ == "__main__":
    asyncio.run(main())

"""Fix 1970-01-01 dates on imported bets by re-fetching from TAB."""
import asyncio
import asyncpg
import random
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/opt/tab-betting-backend")
from betting import legacy_authenticate, get_my_bets

OXYLABS = "http://customer-marolete_86olc-cc-au-sessid-{}-sesstime-10:K5E=2qcyhfyFZs~@pr.oxylabs.io:7777"
DB_URL = "postgresql://tabbetting:tabbetting2026@localhost/tabbetting"

ACCOUNTS = [
    {"label": "SH", "acct": "5457318"},
    {"label": "PJ", "acct": "5471168"},
    {"label": "IanO", "acct": "5475473"},
]


async def main():
    c = await asyncpg.connect(DB_URL)

    # First, check how many bets have bad dates
    bad = await c.fetch(
        "SELECT id, tsn, placed_at FROM bets WHERE username='smd' AND source='manual' "
        "AND (placed_at IS NULL OR placed_at < '2000-01-01'::timestamptz) "
        "ORDER BY id"
    )
    print(f"Bets with bad/null dates: {len(bad)}")
    if bad:
        for b in bad[:5]:
            print(f"  id={b['id']} tsn={b['tsn']} placed_at={b['placed_at']}")

    if not bad:
        print("Nothing to fix!")
        await c.close()
        return

    # Build TSN -> bad bet mapping
    bad_tsns = {b["tsn"]: b["id"] for b in bad if b["tsn"]}
    print(f"Bad TSNs to look up: {len(bad_tsns)}")

    # For each account, fetch TAB bets and match dates
    fixed = 0
    for acc in ACCOUNTS:
        label = acc["label"]
        acct_num = acc["acct"]

        row = await c.fetchrow(
            "SELECT email, password FROM tab_accounts WHERE label=$1 AND username='smd'",
            label,
        )
        proxy = OXYLABS.format(random.randint(1000000000, 9999999999))

        result = legacy_authenticate(acct_num, row["password"], proxy)
        legacy_token = result["legacy_token"]

        tab_data = get_my_bets(legacy_token, acct_num, proxy, count=100, status="ALL", max_pages=30)
        tab_bets = tab_data.get("bets", [])

        print(f"\n{label}: {len(tab_bets)} TAB bets fetched")

        # Check what date fields TAB returns
        if tab_bets:
            sample = tab_bets[0]
            date_keys = [k for k in sample.keys() if "date" in k.lower() or "time" in k.lower() or "placed" in k.lower() or "at" in k.lower()]
            print(f"  Date-related keys: {date_keys}")
            print(f"  Sample bet keys: {list(sample.keys())[:15]}")
            for dk in date_keys:
                print(f"    {dk} = {sample[dk]}")

        for tb in tab_bets:
            tsn = str(tb.get("tsn", ""))
            if tsn not in bad_tsns:
                continue

            bet_id = bad_tsns[tsn]

            # Try various date field names — TAB uses "timestamp"
            placed_str = (
                tb.get("timestamp") or tb.get("placedAt") or tb.get("placed_at") or
                tb.get("placedDate") or tb.get("placed_date") or
                tb.get("date") or tb.get("betDate") or ""
            )

            if not placed_str:
                print(f"  TSN {tsn}: no date field found in TAB data")
                # Print full bet to debug
                print(f"    Full keys: {list(tb.keys())}")
                continue

            # Parse the date
            placed_at = None
            if isinstance(placed_str, (int, float)):
                # Unix epoch
                placed_at = datetime.fromtimestamp(placed_str, tz=timezone.utc)
            elif isinstance(placed_str, str):
                # Try ISO format
                for fmt in [
                    "%Y-%m-%dT%H:%M:%S.%fZ",
                    "%Y-%m-%dT%H:%M:%SZ",
                    "%Y-%m-%dT%H:%M:%S.%f%z",
                    "%Y-%m-%dT%H:%M:%S%z",
                    "%Y-%m-%d %H:%M:%S",
                    "%d/%m/%Y %H:%M:%S",
                ]:
                    try:
                        placed_at = datetime.strptime(placed_str, fmt)
                        if placed_at.tzinfo is None:
                            placed_at = placed_at.replace(tzinfo=timezone.utc)
                        break
                    except ValueError:
                        continue
                if placed_at is None:
                    # Try dateutil as last resort
                    try:
                        from dateutil.parser import parse as dateparse
                        placed_at = dateparse(placed_str)
                        if placed_at.tzinfo is None:
                            placed_at = placed_at.replace(tzinfo=timezone.utc)
                    except:
                        print(f"  TSN {tsn}: could not parse date '{placed_str}'")
                        continue

            if placed_at and placed_at.year > 2000:
                await c.execute(
                    "UPDATE bets SET placed_at = $1 WHERE id = $2",
                    placed_at, bet_id,
                )
                fixed += 1
                print(f"  Fixed TSN {tsn} → {placed_at}")
            else:
                print(f"  TSN {tsn}: parsed date still bad: {placed_at}")

    print(f"\n{'='*60}")
    print(f"TOTAL FIXED: {fixed} / {len(bad)}")

    # Verify
    still_bad = await c.fetchval(
        "SELECT count(*) FROM bets WHERE username='smd' "
        "AND (placed_at IS NULL OR placed_at < '2000-01-01'::timestamptz)"
    )
    print(f"Still bad after fix: {still_bad}")

    await c.close()


asyncio.run(main())

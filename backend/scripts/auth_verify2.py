"""Fix IanO lookup and import missing bets for all 3 accounts."""
import asyncio
import asyncpg
import time
import random
import sys
import json
import uuid

sys.path.insert(0, "/opt/tab-betting-backend")
from betting import legacy_authenticate, get_my_bets

OXYLABS = "http://customer-marolete_86olc-cc-au-sessid-{}-sesstime-10:K5E=2qcyhfyFZs~@pr.oxylabs.io:7777"
DB_URL = "postgresql://tabbetting:tabbetting2026@localhost/tabbetting"


async def main():
    c = await asyncpg.connect(DB_URL)

    # First find IanO's account number from bets table
    # We know from Agent 2 that IanO's bets are under account 5475473
    iano_acct = await c.fetchval(
        "SELECT DISTINCT account_number FROM bets WHERE username='smd' "
        "AND account_number NOT IN ('5457318', '5471168') "
        "AND account_number IS NOT NULL LIMIT 1"
    )
    print(f"IanO account number from bets: {iano_acct}")

    # Get IanO credentials
    iano_row = await c.fetchrow(
        "SELECT email, password FROM tab_accounts WHERE label='IanO' AND username='smd'"
    )
    if not iano_row:
        print("IanO not found in tab_accounts!")
        return

    accounts = [
        {"label": "SH", "acct": "5457318"},
        {"label": "PJ", "acct": "5471168"},
        {"label": "IanO", "acct": iano_acct},
    ]

    # Get credentials for all
    for acc in accounts:
        row = await c.fetchrow(
            "SELECT email, password FROM tab_accounts WHERE label=$1 AND username='smd'",
            acc["label"],
        )
        acc["email"] = row["email"]
        acc["password"] = row["password"]

    total_imported = 0

    for acc in accounts:
        label = acc["label"]
        acct_num = acc["acct"]
        email = acc["email"]
        password = acc["password"]
        proxy = OXYLABS.format(random.randint(1000000000, 9999999999))

        print(f"\n{'='*60}")
        print(f"{label} ({email}) — acct {acct_num}")
        print(f"{'='*60}")

        # Legacy auth
        try:
            result = legacy_authenticate(acct_num, password, proxy)
            legacy_token = result["legacy_token"]
            print(f"  Legacy auth: OK")
        except Exception as e:
            print(f"  Legacy auth: FAILED — {e}")
            continue

        # Fetch TAB bets
        try:
            tab_data = get_my_bets(legacy_token, acct_num, proxy, count=100, status="ALL", max_pages=30)
            tab_bets = tab_data.get("bets", [])
            print(f"  TAB bets: {len(tab_bets)}")
        except Exception as e:
            print(f"  get_my_bets: FAILED — {e}")
            continue

        # Get DB TSNs
        db_tsns_rows = await c.fetch(
            "SELECT tsn FROM bets WHERE account_number=$1 AND username='smd' AND tsn IS NOT NULL",
            acct_num,
        )
        db_tsns = {r["tsn"] for r in db_tsns_rows}
        print(f"  DB TSNs: {len(db_tsns)}")

        # Find missing
        missing = []
        for tb in tab_bets:
            tsn = str(tb.get("tsn", ""))
            if tsn and tsn not in db_tsns:
                missing.append(tb)

        print(f"  Missing from DB: {len(missing)}")

        if not missing:
            print(f"  Nothing to import ✓")
            continue

        # Import missing bets
        for tb in missing:
            tsn = str(tb.get("tsn", ""))
            status = tb.get("status", "Pending")
            stake_raw = tb.get("stake", "0")
            # Parse stake
            stake_str = str(stake_raw).replace("$", "").replace(",", "")
            try:
                stake = float(stake_str)
            except:
                stake = 0.0

            payout_raw = tb.get("payout", "0")
            payout_str = str(payout_raw).replace("$", "").replace(",", "")
            try:
                payout = float(payout_str)
            except:
                payout = 0.0

            # Build legs jsonb
            legs_raw = tb.get("legs", [])
            legs_json = json.dumps(legs_raw) if legs_raw else "[]"

            odds = tb.get("odds", 0)
            bet_type = tb.get("betType", tb.get("bet_type", "Multi"))
            placed_at = tb.get("placedAt", tb.get("placed_at", None))

            bet_id = str(uuid.uuid4())
            await c.execute(
                """INSERT INTO bets (id, username, account_number, account_label, tsn, bet_type, legs, combined_odds, stake, status, payout, source, placed_at)
                   VALUES ($1, 'smd', $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10, 'manual', $11)
                   ON CONFLICT DO NOTHING""",
                bet_id, acct_num, label, tsn, bet_type, legs_json, str(odds), str(stake), status, str(payout),
                placed_at if placed_at else None,
            )
            total_imported += 1
            print(f"    Imported TSN={tsn} status={status} stake=${stake}")

    # Final summary
    print(f"\n{'='*60}")
    print(f"TOTAL IMPORTED: {total_imported}")
    print(f"{'='*60}")

    # Final counts
    rows = await c.fetch("""
        SELECT account_number, status, count(*) as cnt,
               sum(stake::numeric) as staked,
               sum(CASE WHEN payout IS NOT NULL AND payout != '' THEN
                   REPLACE(payout, '$', '')::numeric ELSE 0 END) as returned
        FROM bets WHERE username='smd'
        GROUP BY account_number, status
        ORDER BY account_number, status
    """)
    print(f"\n--- Final State ---")
    for r in rows:
        print(f"  {r['account_number']:10s} | {r['status']:10s} | {r['cnt']:4d} bets | Staked: ${r['staked']:.2f} | Returned: ${r['returned']:.2f}")

    # Overall
    totals = await c.fetchrow("""
        SELECT count(*) as total,
               sum(stake::numeric) as staked,
               sum(CASE WHEN status IN ('Won','Lost','Refunded') THEN stake::numeric ELSE 0 END) as settled_stake,
               sum(CASE WHEN payout IS NOT NULL AND payout != '' THEN
                   REPLACE(payout, '$', '')::numeric ELSE 0 END) as returned
        FROM bets WHERE username='smd'
    """)
    print(f"\n  TOTAL: {totals['total']} bets")
    print(f"  Settled stake: ${totals['settled_stake']:.2f}")
    print(f"  Returned: ${totals['returned']:.2f}")
    print(f"  Net P/L: ${totals['returned'] - totals['settled_stake']:.2f}")

    await c.close()


asyncio.run(main())

"""Clean $ payouts, import missing bets, and produce final maro summary."""
import asyncio
import asyncpg
import random
import uuid
import json
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/opt/tab-betting-backend")
from betting import legacy_authenticate, get_my_bets

DB_URL = "postgresql://tabbetting:tabbetting2026@localhost/tabbetting"
OXYLABS = "http://customer-marolete_86olc-cc-au-sessid-{}-sesstime-10:K5E=2qcyhfyFZs~@pr.oxylabs.io:7777"


async def main():
    c = await asyncpg.connect(DB_URL)

    # 1. Fix $ prefix on all maro payouts
    fixed = await c.execute(
        "UPDATE bets SET payout = REPLACE(payout, '$', '') WHERE username='maro' AND payout LIKE '$%'"
    )
    print(f"Fixed $ prefix payouts: {fixed}")

    # 2. Fix lost bets with non-zero payouts
    fixed2 = await c.execute(
        "UPDATE bets SET payout = '0' WHERE username='maro' AND status='Lost' "
        "AND payout IS NOT NULL AND payout != '0' AND payout != '0.00' AND payout != ''"
    )
    print(f"Fixed lost payouts: {fixed2}")

    # 3. Import missing bets for each maro account
    accounts = await c.fetch(
        "SELECT label, email, password, account_number FROM tab_accounts WHERE username='maro' AND account_number IS NOT NULL ORDER BY label"
    )

    total_imported = 0
    for acc in accounts:
        label = acc["label"]
        acct_num = acc["account_number"]
        email = acc["email"]
        password = acc["password"]

        # Get existing DB TSNs
        db_rows = await c.fetch(
            "SELECT tsn FROM bets WHERE account_number=$1 AND username='maro' AND tsn IS NOT NULL",
            acct_num,
        )
        db_tsns = {r["tsn"] for r in db_rows}

        # Get session legacy token
        sess = await c.fetchrow(
            "SELECT legacy_token FROM tab_sessions WHERE account_number=$1 ORDER BY logged_in_at DESC LIMIT 1",
            acct_num,
        )
        if not sess or not sess["legacy_token"]:
            proxy = OXYLABS.format(random.randint(1000000000, 9999999999))
            try:
                result = legacy_authenticate(acct_num, password, proxy)
                legacy_token = result["legacy_token"]
            except Exception as e:
                print(f"{label}: Auth failed — {e}")
                continue
        else:
            legacy_token = sess["legacy_token"]

        proxy = OXYLABS.format(random.randint(1000000000, 9999999999))
        try:
            tab_data = get_my_bets(legacy_token, acct_num, proxy, count=100, status="ALL", max_pages=30)
            tab_bets = tab_data.get("bets", [])
        except Exception as e:
            print(f"{label}: get_my_bets failed — {e}")
            continue

        # Find missing
        missing = [tb for tb in tab_bets if str(tb.get("tsn", "")) and str(tb.get("tsn", "")) not in db_tsns]

        if not missing:
            print(f"{label} ({acct_num}): Nothing to import ✓")
            continue

        print(f"{label} ({acct_num}): Importing {len(missing)} bets...")

        for tb in missing:
            tsn = str(tb.get("tsn", ""))
            status = tb.get("status", "Pending")
            stake_str = str(tb.get("stake", "0")).replace("$", "").replace(",", "")
            try:
                stake = float(stake_str)
            except:
                stake = 0.0

            payout = "0"
            if status == "Won":
                payout = str(tb.get("payout", "0")).replace("$", "").replace(",", "")
            elif status == "Refunded":
                payout = stake_str

            odds = str(tb.get("odds", 0))
            bet_type = tb.get("type", "Multi")
            legs_raw = tb.get("legs", [])
            legs_json = json.dumps(legs_raw) if legs_raw else "[]"

            # Parse timestamp
            ts_str = tb.get("timestamp", "")
            placed_at = None
            if ts_str:
                for fmt in ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"]:
                    try:
                        placed_at = datetime.strptime(ts_str, fmt).replace(tzinfo=timezone.utc)
                        break
                    except ValueError:
                        continue

            bet_id = str(uuid.uuid4())
            await c.execute(
                """INSERT INTO bets (id, username, account_number, account_label, tsn, bet_type, legs,
                   combined_odds, stake, status, payout, source, placed_at)
                   VALUES ($1, 'maro', $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10, 'manual', $11)
                   ON CONFLICT DO NOTHING""",
                bet_id, acct_num, label, tsn, bet_type, legs_json, odds,
                str(stake), status, payout, placed_at,
            )
            total_imported += 1

        print(f"  Imported {len(missing)}")

    print(f"\nTotal imported: {total_imported}")

    # 4. Final summary
    print(f"\n{'='*60}")
    print("MARO FINAL SUMMARY")
    print(f"{'='*60}")

    rows = await c.fetch("""
        SELECT account_number,
               count(*) FILTER (WHERE status='Won') as won,
               count(*) FILTER (WHERE status='Lost') as lost,
               count(*) FILTER (WHERE status='Refunded') as refunded,
               count(*) FILTER (WHERE status='Pending') as pending,
               sum(CASE WHEN status IN ('Won','Lost','Refunded') THEN stake::numeric ELSE 0 END) as settled_stake,
               sum(CASE WHEN status='Won' AND payout IS NOT NULL AND payout != '' AND payout != '0'
                   THEN payout::numeric WHEN status='Refunded' THEN stake::numeric ELSE 0 END) as returned
        FROM bets WHERE username='maro'
        GROUP BY account_number ORDER BY account_number
    """)

    labels = await c.fetch("SELECT account_number, label FROM tab_accounts WHERE username='maro' AND account_number IS NOT NULL")
    lbl_map = {r["account_number"]: r["label"] for r in labels}

    for r in rows:
        acct = r["account_number"]
        lbl = lbl_map.get(acct, acct)
        settled = float(r["settled_stake"] or 0)
        ret = float(r["returned"] or 0)
        pl = ret - settled
        print(f"  {lbl:5s} ({acct}): W={r['won']} L={r['lost']} R={r['refunded']} P={r['pending']} | Staked: ${settled:.2f} | Returned: ${ret:.2f} | P/L: ${pl:.2f}")

    totals = await c.fetchrow("""
        SELECT count(*) as total,
               count(*) FILTER (WHERE status='Pending') as pending,
               sum(CASE WHEN status IN ('Won','Lost','Refunded') THEN stake::numeric ELSE 0 END) as settled_stake,
               sum(CASE WHEN status='Won' AND payout IS NOT NULL AND payout != '' AND payout != '0'
                   THEN payout::numeric WHEN status='Refunded' THEN stake::numeric ELSE 0 END) as returned
        FROM bets WHERE username='maro'
    """)
    pl = float(totals["returned"]) - float(totals["settled_stake"])
    print(f"\n  TOTAL: {totals['total']} bets ({totals['pending']} pending)")
    print(f"  Settled: ${totals['settled_stake']:.2f} staked, ${totals['returned']:.2f} returned")
    print(f"  Net P/L: ${pl:.2f}")

    await c.close()


asyncio.run(main())

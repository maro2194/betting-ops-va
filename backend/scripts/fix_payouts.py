"""Fix payout values for lost bets and recalculate P/L."""
import asyncio
import asyncpg

DB_URL = "postgresql://tabbetting:tabbetting2026@localhost/tabbetting"


async def main():
    c = await asyncpg.connect(DB_URL)

    # Count lost bets with non-zero payout
    cnt = await c.fetchval(
        "SELECT count(*) FROM bets WHERE username='smd' AND status='Lost' "
        "AND payout IS NOT NULL AND payout != '0' AND payout != '0.00' AND payout != ''"
    )
    print(f"Lost bets with non-zero payout: {cnt}")

    # Fix: set payout to 0 for all lost bets
    if cnt > 0:
        result = await c.execute(
            "UPDATE bets SET payout = '0' WHERE username='smd' AND status='Lost' "
            "AND payout IS NOT NULL AND payout != '0' AND payout != '0.00'"
        )
        print(f"Fixed lost payouts: {result}")

    # Also clean any $ prefix in Won payouts
    cleaned = await c.execute(
        "UPDATE bets SET payout = REPLACE(payout, '$', '') "
        "WHERE username='smd' AND payout LIKE '$%'"
    )
    print(f"Cleaned $ prefix: {cleaned}")

    # Now recalculate corrected P/L
    # Won bets: returned = payout value
    # Lost bets: returned = 0
    # Refunded: returned = stake
    row = await c.fetchrow("""
        SELECT
            count(*) as total,
            sum(stake::numeric) as staked,
            sum(CASE
                WHEN status = 'Won' AND payout IS NOT NULL AND payout != '' AND payout != '0'
                    THEN payout::numeric
                WHEN status = 'Refunded'
                    THEN stake::numeric
                ELSE 0
            END) as returned
        FROM bets
        WHERE username='smd' AND status IN ('Won', 'Lost', 'Refunded')
    """)
    print(f"\n=== Corrected P/L (All Settled) ===")
    print(f"  Settled: {row['total']} bets")
    print(f"  Staked: ${row['staked']:.2f}")
    print(f"  Returned: ${row['returned']:.2f}")
    print(f"  Net P/L: ${row['returned'] - row['staked']:.2f}")
    print(f"  ROI: {(row['returned'] - row['staked']) / row['staked'] * 100:.1f}%")

    # Breakdown by account
    rows = await c.fetch("""
        SELECT
            account_number,
            count(*) FILTER (WHERE status = 'Won') as won,
            count(*) FILTER (WHERE status = 'Lost') as lost,
            count(*) FILTER (WHERE status = 'Refunded') as refunded,
            count(*) FILTER (WHERE status = 'Pending') as pending,
            sum(stake::numeric) FILTER (WHERE status IN ('Won','Lost','Refunded')) as settled_stake,
            sum(CASE
                WHEN status = 'Won' AND payout IS NOT NULL AND payout != '' AND payout != '0'
                    THEN payout::numeric
                WHEN status = 'Refunded' THEN stake::numeric
                ELSE 0
            END) as returned,
            sum(stake::numeric) FILTER (WHERE status = 'Pending') as pending_stake
        FROM bets
        WHERE username='smd'
        GROUP BY account_number
        ORDER BY account_number
    """)

    print(f"\n=== By Account ===")
    acct_labels = {"5457318": "SH", "5471168": "PJ", "5475473": "IanO"}
    for r in rows:
        acct = r["account_number"]
        lbl = acct_labels.get(acct, acct)
        settled = r["settled_stake"] or 0
        ret = r["returned"] or 0
        pl = ret - settled
        print(f"  {lbl} ({acct}): W={r['won']} L={r['lost']} R={r['refunded']} P={r['pending']}")
        print(f"    Settled: ${settled:.2f} staked, ${ret:.2f} returned, P/L=${pl:.2f}")
        if r["pending_stake"]:
            print(f"    Pending: ${r['pending_stake']:.2f} staked")

    # Grand total including pending
    total_row = await c.fetchrow("""
        SELECT count(*) as total, sum(stake::numeric) as total_staked
        FROM bets WHERE username='smd'
    """)
    print(f"\n=== Grand Total ===")
    print(f"  Total bets: {total_row['total']}")
    print(f"  Total staked (all): ${total_row['total_staked']:.2f}")

    await c.close()


asyncio.run(main())

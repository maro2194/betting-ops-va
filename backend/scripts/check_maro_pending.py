import asyncio, asyncpg

async def main():
    c = await asyncpg.connect("postgresql://tabbetting:tabbetting2026@localhost/tabbetting")
    rows = await c.fetch(
        "SELECT id, account_number, account_label, tsn, stake, placed_at "
        "FROM bets WHERE username='maro' AND status='Pending' ORDER BY placed_at"
    )
    print(f"Pending maro bets: {len(rows)}")
    for r in rows:
        acct = r["account_number"]
        label = r["account_label"]
        tsn = r["tsn"]
        stake = r["stake"]
        placed = r["placed_at"]
        print(f"  acct={acct} label={label} tsn={tsn} stake=${stake} placed={placed}")
    await c.close()

asyncio.run(main())

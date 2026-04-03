"""Final verification — confirm DB matches TAB for all 3 accounts."""
import asyncio
import asyncpg
import random
import sys

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

    all_good = True

    for acc in ACCOUNTS:
        label = acc["label"]
        acct_num = acc["acct"]

        row = await c.fetchrow(
            "SELECT email, password FROM tab_accounts WHERE label=$1 AND username='smd'",
            label,
        )
        proxy = OXYLABS.format(random.randint(1000000000, 9999999999))

        # Auth
        result = legacy_authenticate(acct_num, row["password"], proxy)
        legacy_token = result["legacy_token"]

        # Fetch TAB
        tab_data = get_my_bets(legacy_token, acct_num, proxy, count=100, status="ALL", max_pages=30)
        tab_bets = tab_data.get("bets", [])
        tab_tsns = {str(b.get("tsn", "")) for b in tab_bets if b.get("tsn")}

        # Fetch DB
        db_rows = await c.fetch(
            "SELECT tsn FROM bets WHERE account_number=$1 AND username='smd' AND tsn IS NOT NULL",
            acct_num,
        )
        db_tsns = {r["tsn"] for r in db_rows}

        in_db_not_tab = db_tsns - tab_tsns
        in_tab_not_db = tab_tsns - db_tsns

        status = "MATCH" if not in_db_not_tab and not in_tab_not_db else "MISMATCH"
        if status == "MISMATCH":
            all_good = False

        print(f"{label} ({acct_num}): DB={len(db_tsns)} TAB={len(tab_tsns)} → {status}")
        if in_db_not_tab:
            print(f"  In DB not TAB: {len(in_db_not_tab)}")
        if in_tab_not_db:
            print(f"  In TAB not DB: {len(in_tab_not_db)}")

    await c.close()

    print()
    if all_good:
        print("ALL ACCOUNTS VERIFIED — DB matches TAB perfectly")
    else:
        print("MISMATCHES FOUND — needs investigation")


asyncio.run(main())

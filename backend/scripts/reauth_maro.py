"""Re-authenticate maro accounts with expired tokens and check results."""
import asyncio
import asyncpg
import random
import time
import sys

sys.path.insert(0, "/opt/tab-betting-backend")
from betting import legacy_authenticate, get_my_bets

DB_URL = "postgresql://tabbetting:tabbetting2026@localhost/tabbetting"
OXYLABS = "http://customer-marolete_86olc-cc-au-sessid-{}-sesstime-10:K5E=2qcyhfyFZs~@pr.oxylabs.io:7777"


async def main():
    c = await asyncpg.connect(DB_URL)

    # Get all maro accounts with their sessions
    accounts = await c.fetch(
        "SELECT label, email, password, account_number FROM tab_accounts WHERE username='maro' ORDER BY label"
    )

    for acc in accounts:
        label = acc["label"]
        email = acc["email"]
        password = acc["password"]
        acct_num = acc["account_number"]

        print(f"\n{'='*50}")
        print(f"{label} ({email}) acct={acct_num}")

        # If no account number, look it up from tab_sessions
        if not acct_num:
            sess = await c.fetchrow(
                "SELECT account_number FROM tab_sessions WHERE email=$1 ORDER BY logged_in_at DESC LIMIT 1",
                email,
            )
            if sess and sess["account_number"]:
                acct_num = sess["account_number"]
                print(f"  Found acct from session: {acct_num}")
                # Update tab_accounts
                await c.execute(
                    "UPDATE tab_accounts SET account_number=$1 WHERE email=$2 AND username='maro'",
                    acct_num, email,
                )
            else:
                print(f"  SKIP — no account number anywhere")
                continue

        proxy = OXYLABS.format(random.randint(1000000000, 9999999999))

        # Re-authenticate legacy
        try:
            result = legacy_authenticate(acct_num, password, proxy)
            legacy_token = result["legacy_token"]
            print(f"  Legacy auth: OK")
        except Exception as e:
            print(f"  Legacy auth: FAILED — {e}")
            continue

        # Update the tab_session with new legacy token
        await c.execute(
            "UPDATE tab_sessions SET legacy_token=$1, logged_in_at=$2 WHERE account_number=$3",
            legacy_token, time.time(), acct_num,
        )

        # Fetch bets and check for settled ones
        try:
            tab_data = get_my_bets(legacy_token, acct_num, proxy, count=100, status="ALL", max_pages=30)
            tab_bets = tab_data.get("bets", [])
            print(f"  TAB bets: {len(tab_bets)}")
        except Exception as e:
            print(f"  get_my_bets: FAILED — {e}")
            continue

        # Build TAB lookup
        tab_by_tsn = {}
        for tb in tab_bets:
            tsn = str(tb.get("tsn", ""))
            if tsn:
                tab_by_tsn[tsn] = tb

        # Get pending DB bets for this account
        pending = await c.fetch(
            "SELECT id, tsn, stake, status FROM bets WHERE account_number=$1 AND username='maro' AND status='Pending'",
            acct_num,
        )
        print(f"  DB pending: {len(pending)}")

        # Settle
        settled = 0
        for bet in pending:
            tsn = bet["tsn"]
            if not tsn or tsn not in tab_by_tsn:
                continue
            tab_bet = tab_by_tsn[tsn]
            tab_status = tab_bet.get("status", "")

            if tab_status in ("Won", "Lost", "Refunded"):
                payout = "0"
                if tab_status == "Won":
                    payout = str(tab_bet.get("payout", "0")).replace("$", "")
                elif tab_status == "Refunded":
                    payout = str(bet["stake"])

                await c.execute(
                    "UPDATE bets SET status=$1, payout=$2 WHERE id=$3",
                    tab_status, payout, bet["id"],
                )
                settled += 1

        print(f"  Settled: {settled}")

    # Also check for any missing bets (on TAB not in DB)
    print(f"\n{'='*50}")
    print("Checking for missing bets...")

    all_maro_accts = await c.fetch(
        "SELECT DISTINCT account_number FROM tab_accounts WHERE username='maro' AND account_number IS NOT NULL"
    )

    for row in all_maro_accts:
        acct_num = row["account_number"]
        db_tsns = await c.fetch(
            "SELECT tsn FROM bets WHERE account_number=$1 AND username='maro' AND tsn IS NOT NULL",
            acct_num,
        )
        db_tsn_set = {r["tsn"] for r in db_tsns}

        # Get label
        lbl = await c.fetchval(
            "SELECT label FROM tab_accounts WHERE account_number=$1 AND username='maro'",
            acct_num,
        )

        sess = await c.fetchrow(
            "SELECT legacy_token FROM tab_sessions WHERE account_number=$1 ORDER BY logged_in_at DESC LIMIT 1",
            acct_num,
        )
        if not sess or not sess["legacy_token"]:
            continue

        proxy = OXYLABS.format(random.randint(1000000000, 9999999999))
        try:
            tab_data = get_my_bets(sess["legacy_token"], acct_num, proxy, count=100, status="ALL", max_pages=30)
            tab_bets = tab_data.get("bets", [])
        except:
            continue

        tab_tsn_set = {str(b.get("tsn", "")) for b in tab_bets if b.get("tsn")}
        missing = tab_tsn_set - db_tsn_set

        if missing:
            print(f"  {lbl} ({acct_num}): {len(missing)} bets on TAB not in DB")
        else:
            print(f"  {lbl} ({acct_num}): All match ✓ (DB={len(db_tsn_set)}, TAB={len(tab_tsn_set)})")

    # Final summary
    print(f"\n{'='*50}")
    print("FINAL MARO SUMMARY")
    rows = await c.fetch("""
        SELECT account_number, status, count(*) as cnt, sum(stake::numeric) as staked,
               sum(CASE WHEN status='Won' AND payout IS NOT NULL AND payout != '' AND payout != '0'
                   THEN payout::numeric WHEN status='Refunded' THEN stake::numeric ELSE 0 END) as returned
        FROM bets WHERE username='maro'
        GROUP BY account_number, status ORDER BY account_number, status
    """)
    for r in rows:
        print(f"  {r['account_number']:10s} | {r['status']:10s} | {r['cnt']:3d} bets | ${r['staked']:.2f} staked | ${r['returned']:.2f} returned")

    totals = await c.fetchrow("""
        SELECT count(*) as total,
               sum(CASE WHEN status IN ('Won','Lost','Refunded') THEN stake::numeric ELSE 0 END) as settled_stake,
               sum(CASE WHEN status='Won' AND payout IS NOT NULL AND payout != '' AND payout != '0'
                   THEN payout::numeric WHEN status='Refunded' THEN stake::numeric ELSE 0 END) as returned,
               count(*) FILTER (WHERE status='Pending') as pending
        FROM bets WHERE username='maro'
    """)
    pl = float(totals["returned"]) - float(totals["settled_stake"])
    print(f"\n  Total: {totals['total']} bets | Pending: {totals['pending']}")
    print(f"  Settled: ${totals['settled_stake']:.2f} staked, ${totals['returned']:.2f} returned")
    print(f"  Net P/L: ${pl:.2f}")

    await c.close()


asyncio.run(main())

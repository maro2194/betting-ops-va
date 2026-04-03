"""Authenticate SH and IanO via legacy auth, then cross-check bets against DB."""
import asyncio
import asyncpg
import time
import random
import sys

sys.path.insert(0, "/opt/tab-betting-backend")
from betting import legacy_authenticate, get_my_bets

OXYLABS = "http://customer-marolete_86olc-cc-au-sessid-{}-sesstime-10:K5E=2qcyhfyFZs~@pr.oxylabs.io:7777"
DB_URL = "postgresql://tabbetting:tabbetting2026@localhost/tabbetting"


async def main():
    c = await asyncpg.connect(DB_URL)

    # Get SH and IanO credentials from tab_accounts
    for label in ["SH", "IanO", "PJ"]:
        row = await c.fetchrow(
            "SELECT email, password FROM tab_accounts WHERE label=$1 AND username=$2",
            label, "smd",
        )
        if not row:
            print(f"\n{label}: NOT FOUND in tab_accounts")
            continue

        email = row["email"]
        password = row["password"]
        print(f"\n{'='*60}")
        print(f"{label} ({email})")
        print(f"{'='*60}")

        # Find existing session to get the account number
        sess = await c.fetchrow(
            "SELECT account_number, legacy_token, token_exp FROM tab_sessions WHERE email=$1 ORDER BY logged_in_at DESC LIMIT 1",
            email,
        )
        if sess:
            acct_num = sess["account_number"]
            exp = float(sess["token_exp"]) if sess["token_exp"] else 0
            print(f"  Session found: acct={acct_num}, token_valid={time.time() < exp}")
        else:
            print(f"  No existing session — need account number from somewhere")
            # Check bets table for this email pattern
            acct_row = await c.fetchrow(
                "SELECT DISTINCT account_number FROM bets WHERE username='smd' AND account_number IN "
                "(SELECT account_number FROM tab_sessions WHERE email=$1)",
                email,
            )
            if acct_row:
                acct_num = acct_row["account_number"]
                print(f"  Found acct from bets: {acct_num}")
            else:
                print(f"  SKIP — cannot determine account number")
                continue

        proxy = OXYLABS.format(random.randint(1000000000, 9999999999))
        print(f"  Proxy: Oxylabs rotating")

        # Legacy authenticate
        try:
            result = legacy_authenticate(acct_num, password, proxy)
            legacy_token = result["legacy_token"]
            print(f"  Legacy auth: OK")
        except Exception as e:
            print(f"  Legacy auth: FAILED — {e}")
            continue

        # Fetch ALL bets from TAB (high pagination)
        try:
            tab_data = get_my_bets(legacy_token, acct_num, proxy, count=100, status="ALL", max_pages=30)
            tab_bets = tab_data.get("bets", [])
            print(f"  TAB bets fetched: {len(tab_bets)}")
        except Exception as e:
            print(f"  get_my_bets: FAILED — {e}")
            continue

        # Get DB bets
        db_bets = await c.fetch(
            "SELECT id, tsn, stake, status, placed_at, payout FROM bets WHERE account_number=$1 AND username='smd'",
            acct_num,
        )
        db_tsns = {b["tsn"] for b in db_bets if b["tsn"]}

        # TAB TSN set
        tab_tsns = {str(b.get("tsn", "")) for b in tab_bets if b.get("tsn")}

        print(f"  DB: {len(db_bets)} bets ({len(db_tsns)} unique TSNs)")
        print(f"  TAB: {len(tab_bets)} bets ({len(tab_tsns)} unique TSNs)")

        # Compare
        in_db_not_tab = db_tsns - tab_tsns
        in_tab_not_db = tab_tsns - db_tsns

        if in_db_not_tab:
            print(f"  !! {len(in_db_not_tab)} bets in DB but NOT on TAB:")
            for t in sorted(in_db_not_tab)[:10]:
                bet = next((b for b in db_bets if b["tsn"] == t), None)
                if bet:
                    print(f"     TSN={t} stake=${bet['stake']} status={bet['status']}")
        else:
            print(f"  DB->TAB: All match ✓")

        if in_tab_not_db:
            print(f"  !! {len(in_tab_not_db)} bets on TAB but NOT in DB:")
            for t in sorted(in_tab_not_db)[:10]:
                tb = next((b for b in tab_bets if str(b.get("tsn", "")) == t), None)
                if tb:
                    print(f"     TSN={t} status={tb.get('status')}")
        else:
            print(f"  TAB->DB: All match ✓")

        # Check for pending in DB that are settled on TAB
        db_pending = {b["tsn"]: b for b in db_bets if b["tsn"] and b["status"] == "Pending"}
        tab_by_tsn = {str(b.get("tsn", "")): b for b in tab_bets}

        still_pending_on_tab = 0
        settled_on_tab = 0
        for tsn, db_bet in db_pending.items():
            tab_bet = tab_by_tsn.get(tsn)
            if tab_bet:
                ts = tab_bet.get("status", "")
                if ts in ("Won", "Lost", "Refunded"):
                    settled_on_tab += 1
                else:
                    still_pending_on_tab += 1

        print(f"  DB Pending: {len(db_pending)}")
        print(f"    Still pending on TAB: {still_pending_on_tab}")
        print(f"    Settled on TAB (need update): {settled_on_tab}")

    await c.close()
    print(f"\n{'='*60}")
    print("DONE")


asyncio.run(main())

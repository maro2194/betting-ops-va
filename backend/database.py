"""Database module for TAB account and session persistence."""
import asyncpg
import logging
import uuid as _uuid
import time

logger = logging.getLogger(__name__)

DATABASE_URL = "postgresql://tabbetting:tabbetting2026@localhost/tabbetting"

pool = None


async def init_db():
    """Initialize connection pool and create tables."""
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    async with pool.acquire() as conn:
        # TAB accounts
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tab_accounts (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                label TEXT NOT NULL,
                email TEXT NOT NULL,
                password TEXT NOT NULL,
                proxy_url TEXT NOT NULL,
                account_number TEXT,
                customer_id TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_tab_accounts_user_email
            ON tab_accounts(username, email)
        """)

        # App auth sessions (persistent login)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS app_sessions (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                name TEXT NOT NULL,
                created_at DOUBLE PRECISION NOT NULL
            )
        """)

        # TAB sessions (persistent TAB login state)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tab_sessions (
                session_id TEXT PRIMARY KEY,
                auth0_token TEXT NOT NULL,
                legacy_token TEXT,
                account_number TEXT NOT NULL,
                customer_id TEXT NOT NULL,
                email TEXT NOT NULL,
                password TEXT NOT NULL,
                proxy_url TEXT NOT NULL,
                logged_in_at DOUBLE PRECISION NOT NULL,
                token_exp DOUBLE PRECISION
            )
        """)

        # Bets placed through this app
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bets (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                account_number TEXT NOT NULL,
                account_label TEXT NOT NULL,
                tsn TEXT,
                bet_type TEXT NOT NULL,
                legs JSONB NOT NULL,
                combined_odds TEXT NOT NULL,
                stake TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Pending',
                payout TEXT,
                placed_at TIMESTAMPTZ DEFAULT NOW(),
                settled_at TIMESTAMPTZ,
                raw_response JSONB,
                source TEXT
            )
        """)
        # Add source column if missing (migration for existing DBs)
        await conn.execute("""
            DO $$ BEGIN
                ALTER TABLE bets ADD COLUMN IF NOT EXISTS source TEXT;
            EXCEPTION WHEN duplicate_column THEN NULL;
            END $$;
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_bets_username ON bets(username)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_bets_status ON bets(status)
        """)

    logger.info("Database initialized")


async def close_db():
    global pool
    if pool:
        await pool.close()


# ─── App Sessions ────────────────────────────────────────────────────────────

async def save_app_session(token: str, username: str, name: str, created_at: float):
    """Save an app auth session to DB."""
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO app_sessions (token, username, name, created_at)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (token) DO NOTHING""",
            token, username, name, created_at,
        )


async def get_app_session(token: str) -> dict | None:
    """Get an app auth session from DB."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM app_sessions WHERE token = $1", token,
        )
        return dict(row) if row else None


async def delete_app_session(token: str):
    """Delete an app auth session."""
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM app_sessions WHERE token = $1", token)


async def load_all_app_sessions() -> dict:
    """Load all app sessions into memory on startup."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM app_sessions")
        return {
            r["token"]: {"username": r["username"], "name": r["name"], "created_at": r["created_at"]}
            for r in rows
        }


# ─── TAB Sessions ────────────────────────────────────────────────────────────

async def save_tab_session(session_id: str, data: dict):
    """Save a TAB session to DB."""
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO tab_sessions (session_id, auth0_token, legacy_token, account_number,
                   customer_id, email, password, proxy_url, logged_in_at, token_exp)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
               ON CONFLICT (session_id) DO UPDATE SET
                   auth0_token = EXCLUDED.auth0_token,
                   legacy_token = EXCLUDED.legacy_token,
                   logged_in_at = EXCLUDED.logged_in_at,
                   token_exp = EXCLUDED.token_exp""",
            session_id,
            data["token"],
            data.get("legacy_token", ""),
            data["account_number"],
            data["customer_id"],
            data["email"],
            data["password"],
            data["proxy_url"],
            data["logged_in_at"],
            data.get("token_exp"),
        )


async def load_all_tab_sessions() -> dict:
    """Load all TAB sessions into memory on startup."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM tab_sessions")
        result = {}
        for r in rows:
            result[r["session_id"]] = {
                "token": r["auth0_token"],
                "legacy_token": r["legacy_token"] or "",
                "account_number": r["account_number"],
                "customer_id": r["customer_id"],
                "email": r["email"],
                "password": r["password"],
                "proxy_url": r["proxy_url"],
                "profile_dir": "",
                "logged_in_at": r["logged_in_at"],
            }
        return result


async def delete_tab_session(session_id: str):
    """Delete a TAB session."""
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tab_sessions WHERE session_id = $1", session_id)


# ─── Account CRUD ────────────────────────────────────────────────────────────

async def get_accounts(username: str) -> list[dict]:
    """Get all accounts for a user."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM tab_accounts WHERE username = $1 ORDER BY created_at",
            username,
        )
        return [dict(r) for r in rows]


async def upsert_account(username: str, account: dict) -> dict:
    """Insert or update an account."""
    aid = account.get("id") or str(_uuid.uuid4())
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO tab_accounts (id, username, label, email, password, proxy_url, account_number, customer_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (id) DO UPDATE SET
                label = EXCLUDED.label,
                email = EXCLUDED.email,
                password = EXCLUDED.password,
                proxy_url = EXCLUDED.proxy_url,
                account_number = EXCLUDED.account_number,
                customer_id = EXCLUDED.customer_id,
                updated_at = NOW()
            """,
            aid,
            username,
            account["label"],
            account["email"],
            account["password"],
            account["proxy_url"],
            account.get("account_number"),
            account.get("customer_id"),
        )
    account["id"] = aid
    return account


async def delete_account(username: str, account_id: str):
    """Delete an account."""
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM tab_accounts WHERE id = $1 AND username = $2",
            account_id,
            username,
        )


async def sync_accounts(username: str, accounts: list[dict]):
    """Bulk sync accounts from frontend (replaces all accounts for a user)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetch(
                "SELECT id FROM tab_accounts WHERE username = $1", username
            )
            existing_ids = {r["id"] for r in existing}
            incoming_ids = {a["id"] for a in accounts if "id" in a}

            to_delete = existing_ids - incoming_ids
            if to_delete:
                await conn.execute(
                    "DELETE FROM tab_accounts WHERE username = $1 AND id = ANY($2::text[])",
                    username,
                    list(to_delete),
                )

            for a in accounts:
                aid = a.get("id") or str(_uuid.uuid4())
                await conn.execute(
                    """
                    INSERT INTO tab_accounts (id, username, label, email, password, proxy_url, account_number, customer_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (id) DO UPDATE SET
                        label = EXCLUDED.label,
                        email = EXCLUDED.email,
                        password = EXCLUDED.password,
                        proxy_url = EXCLUDED.proxy_url,
                        account_number = EXCLUDED.account_number,
                        customer_id = EXCLUDED.customer_id,
                        updated_at = NOW()
                    """,
                    aid,
                    username,
                    a.get("label", ""),
                    a["email"],
                    a["password"],
                    a["proxy_url"],
                    a.get("account_number"),
                    a.get("customer_id"),
                )


# ─── Bets ────────────────────────────────────────────────────────────────────

async def save_bet(username: str, bet: dict) -> str:
    """Save a placed bet to DB. Returns bet ID."""
    import json
    bet_id = bet.get("id") or str(_uuid.uuid4())
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO bets (id, username, account_number, account_label, tsn, bet_type,
                   legs, combined_odds, stake, status, payout, raw_response, source)
               VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11, $12::jsonb, $13)""",
            bet_id,
            username,
            bet["account_number"],
            bet.get("account_label", ""),
            bet.get("tsn"),
            bet.get("bet_type", "SGM"),
            json.dumps(bet["legs"]),
            bet["combined_odds"],
            bet["stake"],
            bet.get("status", "Pending"),
            bet.get("payout"),
            json.dumps(bet.get("raw_response")) if bet.get("raw_response") else None,
            bet.get("source"),
        )
    return bet_id


async def get_bets(username: str, status: str = None, account_number: str = None,
                   account_label: str = None, date_from: str = None, date_to: str = None,
                   limit: int = 2000) -> list[dict]:
    """Get bets from DB with optional filters."""
    import json
    query = "SELECT * FROM bets WHERE username = $1"
    params = [username]
    idx = 2
    if status:
        query += f" AND status = ${idx}"
        params.append(status)
        idx += 1
    if account_number:
        query += f" AND account_number = ${idx}"
        params.append(account_number)
        idx += 1
    if account_label:
        query += f" AND account_label = ${idx}"
        params.append(account_label)
        idx += 1
    if date_from:
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        # Convert local AEST date to UTC (AEST = UTC+10)
        aest = _tz(_td(hours=10))
        dt_from = _dt.fromisoformat(date_from).replace(tzinfo=aest)
        query += f" AND placed_at >= ${idx}"
        params.append(dt_from)
        idx += 1
    if date_to:
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        aest = _tz(_td(hours=10))
        dt_to = _dt.fromisoformat(date_to + "T23:59:59").replace(tzinfo=aest)
        query += f" AND placed_at <= ${idx}"
        params.append(dt_to)
        idx += 1
    query += f" ORDER BY placed_at DESC LIMIT ${idx}"
    params.append(limit)

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
        bets = []
        for r in rows:
            bet = dict(r)
            # JSONB comes back as str or dict depending on asyncpg version
            if isinstance(bet.get("legs"), str):
                bet["legs"] = json.loads(bet["legs"])
            if isinstance(bet.get("raw_response"), str):
                bet["raw_response"] = json.loads(bet["raw_response"])
            bet["placed_at"] = bet["placed_at"].isoformat() if bet.get("placed_at") else None
            bet["settled_at"] = bet["settled_at"].isoformat() if bet.get("settled_at") else None
            bets.append(bet)
        return bets


async def update_bet_status(bet_id: str, status: str, payout: str = None):
    """Update a bet's status (Won/Lost/Pending)."""
    async with pool.acquire() as conn:
        if status in ("Won", "Lost"):
            await conn.execute(
                "UPDATE bets SET status = $1, payout = $2, settled_at = NOW() WHERE id = $3",
                status, payout, bet_id,
            )
        else:
            await conn.execute(
                "UPDATE bets SET status = $1 WHERE id = $2",
                status, bet_id,
            )


async def get_all_tsns(username: str = None) -> set:
    """Return a set of all TSNs already recorded in the bets table.
    Checks ALL users to prevent cross-user duplicates (e.g. Shadow places via BotOps,
    Maro syncs the same account — same TSN, different username)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT tsn FROM bets WHERE tsn IS NOT NULL AND tsn != 'None' AND tsn != ''",
        )
        return {r["tsn"] for r in rows}


async def get_pending_bets(username: str = None) -> list[dict]:
    """Get all pending bets, optionally filtered by username."""
    import json
    if username:
        rows_raw = await pool.fetch(
            "SELECT * FROM bets WHERE status = 'Pending' AND username = $1 ORDER BY placed_at DESC",
            username,
        )
    else:
        rows_raw = await pool.fetch(
            "SELECT * FROM bets WHERE status = 'Pending' ORDER BY placed_at DESC",
        )
    bets = []
    for r in rows_raw:
        bet = dict(r)
        if isinstance(bet.get("legs"), str):
            bet["legs"] = json.loads(bet["legs"])
        bets.append(bet)
    return bets

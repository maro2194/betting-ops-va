"""Database tables and CRUD for multi-bookie account registry, CSV batches, and bet rows."""
import json
import logging
import uuid as _uuid

from database import pool

logger = logging.getLogger(__name__)


async def init_multi_db():
    """Create multi-bookie tables if they don't exist."""
    async with pool.acquire() as conn:
        # Multi-bookie account registry
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bookie_accounts (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                initials TEXT NOT NULL,
                owner_name TEXT NOT NULL,
                platform TEXT NOT NULL,
                brand TEXT NOT NULL,
                email TEXT NOT NULL,
                password TEXT NOT NULL,
                proxy_base TEXT NOT NULL DEFAULT '',
                brand_config JSONB DEFAULT '{}',
                account_number TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_bookie_initials_brand
                ON bookie_accounts(username, initials, brand)
        """)

        # CSV batch tracking
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS csv_batches (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                filename TEXT,
                uploaded_at TIMESTAMPTZ DEFAULT NOW(),
                total_rows INTEGER NOT NULL DEFAULT 0,
                status TEXT DEFAULT 'pending',
                summary JSONB DEFAULT '{}'
            )
        """)

        # Individual bet rows
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS csv_bet_rows (
                id TEXT PRIMARY KEY,
                batch_id TEXT REFERENCES csv_batches(id),
                row_index INTEGER NOT NULL,
                date TEXT, track TEXT, race TEXT, horse TEXT,
                units TEXT, target_odds TEXT, owner TEXT, initials TEXT,
                bookmaker TEXT, stake_type TEXT, stake DOUBLE PRECISION,
                promotion TEXT, target_stake TEXT,
                platform TEXT, brand TEXT,
                account_id TEXT,
                runner_id TEXT,
                live_odds TEXT,
                status TEXT DEFAULT 'pending',
                error TEXT,
                bet_reference TEXT,
                placed_at TIMESTAMPTZ,
                raw_response JSONB
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_csv_rows_batch ON csv_bet_rows(batch_id)
        """)

    logger.info("Multi-bookie database tables initialized")


# ─── Bookie Accounts CRUD ──────────────────────────────────────────────────


async def get_bookie_accounts(username: str) -> list[dict]:
    """List all bookie accounts for a user."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM bookie_accounts WHERE username = $1 ORDER BY created_at",
            username,
        )
        result = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("brand_config"), str):
                d["brand_config"] = json.loads(d["brand_config"])
            result.append(d)
        return result


async def upsert_bookie_account(data: dict) -> dict:
    """Create or update a bookie account."""
    aid = data.get("id") or str(_uuid.uuid4())
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO bookie_accounts
                (id, username, initials, owner_name, platform, brand, email, password,
                 proxy_base, brand_config, account_number, is_active)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, $12)
            ON CONFLICT (id) DO UPDATE SET
                initials = EXCLUDED.initials,
                owner_name = EXCLUDED.owner_name,
                platform = EXCLUDED.platform,
                brand = EXCLUDED.brand,
                email = EXCLUDED.email,
                password = EXCLUDED.password,
                proxy_base = EXCLUDED.proxy_base,
                brand_config = EXCLUDED.brand_config,
                account_number = EXCLUDED.account_number,
                is_active = EXCLUDED.is_active,
                updated_at = NOW()
            """,
            aid,
            data["username"],
            data["initials"],
            data["owner_name"],
            data["platform"],
            data["brand"],
            data["email"],
            data["password"],
            data.get("proxy_base", ""),
            json.dumps(data.get("brand_config", {})),
            data.get("account_number"),
            data.get("is_active", True),
        )
    data["id"] = aid
    return data


async def delete_bookie_account(account_id: str):
    """Delete a bookie account by ID."""
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM bookie_accounts WHERE id = $1", account_id)


async def find_account_by_initials_brand(username: str, initials: str, brand: str) -> dict | None:
    """Find a bookie account by username + initials + brand (CSV lookup)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT * FROM bookie_accounts
               WHERE username = $1 AND initials = $2 AND brand = $3 AND is_active = TRUE""",
            username, initials.upper(), brand.lower(),
        )
        if not row:
            return None
        d = dict(row)
        if isinstance(d.get("brand_config"), str):
            d["brand_config"] = json.loads(d["brand_config"])
        return d


# ─── CSV Batches CRUD ──────────────────────────────────────────────────────


async def create_csv_batch(batch_id: str, username: str, filename: str, total_rows: int):
    """Create a new CSV batch record."""
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO csv_batches (id, username, filename, total_rows, status)
               VALUES ($1, $2, $3, $4, 'pending')""",
            batch_id, username, filename, total_rows,
        )


async def insert_csv_rows(rows: list[dict]):
    """Bulk insert CSV bet rows."""
    if not rows:
        return
    async with pool.acquire() as conn:
        for r in rows:
            await conn.execute(
                """INSERT INTO csv_bet_rows
                    (id, batch_id, row_index, date, track, race, horse,
                     units, target_odds, owner, initials, bookmaker, stake_type, stake,
                     promotion, target_stake, platform, brand, account_id, status)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20)""",
                r["id"], r["batch_id"], r["row_index"],
                r.get("date"), r.get("track"), r.get("race"), r.get("horse"),
                r.get("units"), r.get("target_odds"), r.get("owner"), r.get("initials"),
                r.get("bookmaker"), r.get("stake_type"), r.get("stake"),
                r.get("promotion"), r.get("target_stake"),
                r.get("platform"), r.get("brand"), r.get("account_id"),
                r.get("status", "pending"),
            )


async def update_csv_row(row_id: str, updates: dict):
    """Update fields on a CSV bet row."""
    if not updates:
        return
    set_clauses = []
    params = []
    idx = 1
    for key, val in updates.items():
        if key == "raw_response":
            set_clauses.append(f"{key} = ${idx}::jsonb")
            params.append(json.dumps(val) if val is not None else None)
        else:
            set_clauses.append(f"{key} = ${idx}")
            params.append(val)
        idx += 1
    params.append(row_id)
    query = f"UPDATE csv_bet_rows SET {', '.join(set_clauses)} WHERE id = ${idx}"
    async with pool.acquire() as conn:
        await conn.execute(query, *params)


async def get_batch_status(batch_id: str) -> dict | None:
    """Get a batch with all its rows."""
    async with pool.acquire() as conn:
        batch_row = await conn.fetchrow(
            "SELECT * FROM csv_batches WHERE id = $1", batch_id,
        )
        if not batch_row:
            return None
        batch = dict(batch_row)
        if isinstance(batch.get("summary"), str):
            batch["summary"] = json.loads(batch["summary"])

        rows = await conn.fetch(
            "SELECT * FROM csv_bet_rows WHERE batch_id = $1 ORDER BY row_index",
            batch_id,
        )
        bet_rows = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("raw_response"), str):
                d["raw_response"] = json.loads(d["raw_response"])
            # Convert timestamps to ISO strings
            if d.get("placed_at"):
                d["placed_at"] = d["placed_at"].isoformat()
            bet_rows.append(d)

        batch["rows"] = bet_rows
        if batch.get("uploaded_at"):
            batch["uploaded_at"] = batch["uploaded_at"].isoformat()
        return batch


async def update_batch_summary(batch_id: str, summary: dict):
    """Update a batch's summary and status."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE csv_batches SET summary = $1::jsonb, status = $2 WHERE id = $3",
            json.dumps(summary),
            summary.get("status", "completed"),
            batch_id,
        )


async def get_recent_batches(username: str, limit: int = 20) -> list[dict]:
    """List recent CSV batches for a user."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, username, filename, uploaded_at, total_rows, status, summary
               FROM csv_batches WHERE username = $1
               ORDER BY uploaded_at DESC LIMIT $2""",
            username, limit,
        )
        result = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("summary"), str):
                d["summary"] = json.loads(d["summary"])
            if d.get("uploaded_at"):
                d["uploaded_at"] = d["uploaded_at"].isoformat()
            result.append(d)
        return result

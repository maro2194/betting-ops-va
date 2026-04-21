"""
One-way sync of confirmed BotOps bets into the external BetOps tracker
(https://www.betops.sh/api/bets).

Entry point: emit_to_betops(bet, username) — called from database.save_bet right
after a bet row is inserted. The call is fire-and-forget: any failure (BetOps
down, 5xx, network partition) drops the payload into the betops_outbox table
and a background task retries with exponential backoff.

Design notes:
- save_bet is only called on CONFIRMED placements, so the spec's "only place
  what actually gets confirmed" requirement is satisfied automatically.
- Failures are logged but never bubble up — BetOps being offline must not
  block or corrupt local bet persistence.
- Outbox is keyed by bet_id (our UUID), so accidental double-enqueue is a
  safe UPSERT and not a duplicate POST.
- Permanent failures (400/404 — bad payload or missing bookmaker/account on
  BetOps side) stop retrying; operator reviews via the outbox table.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BETOPS_URL = os.environ.get("BETOPS_URL", "https://www.betops.sh/api/bets")
BETOPS_TIMEOUT = 10.0

# Source tags in BotOps that represent promo activity. Everything else maps to non_promo.
# Bonus-bet detection is deliberately omitted for this first pass per operator instruction.
PROMO_SOURCE_TAGS = {
    "racing_promo", "racing_alloc_promo", "sb_promo", "amused_promo",
    "bet365_megaboost", "tab_promo", "promo",
}

# Default bookmaker for the TAB save_bet path (everything routed through save_bet is TAB).
DEFAULT_BOOKMAKER = "TAB"

# Bookmaker name mapping for non-TAB platforms.
# Key format: (platform, brand) — both lowercased. Values are the strings BetOps expects.
#
# The operator will provide the authoritative list. Until then, these are best-guess
# defaults that mirror common industry names. If BetOps returns 404 for a bookmaker,
# the entry lands in betops_outbox with permanent_failure=TRUE; fix the mapping here
# and flip the row's permanent_failure back to FALSE to retry.
#
# Env override: set BETOPS_BOOKMAKER_MAP to a JSON dict like
#   {"amused/betnation": "Betnation", "betmakers/crownbet": "Crownbet"}
# and it will merge on top of these defaults at import time.
BOOKMAKER_MAP: dict = {
    # ── Direct platforms (platform string == bookmaker name) ──────────────
    ("tab", ""): "TAB", ("tab", "tab"): "TAB",
    ("bet365", ""): "Bet365", ("bet365", "bet365"): "Bet365",
    ("sportsbet", ""): "Sportsbet", ("sportsbet", "sportsbet"): "Sportsbet",
    ("pointsbet", ""): "Pointsbet", ("pointsbet", "pointsbet"): "Pointsbet",
    ("palmerbet", ""): "Palmerbet",
    ("betr", ""): "Betr",
    ("betright", ""): "Betright",
    ("tabtouch", ""): "TabTouch",
    ("picklebet", ""): "Picklebet",
    ("dabble", ""): "Dabble",
    ("betblitz", ""): "Betblitz",
    ("nextbet", ""): "Nextbet",

    # ── Amused family (corporate tech, shared platform=amused) ─────────────
    ("amused", "betnation"):   "Betnation",
    ("amused", "betdeluxe"):   "Betdeluxe",
    ("amused", "betexpress"):  "Betexpress",
    ("amused", "betjet"):      "Betjet",
    ("amused", "bigbet"):      "Bigbet",
    ("amused", "mightybet"):   "Mightybet",
    ("amused", "pulsebet"):    "Pulsebet",
    ("amused", "surge"):       "Surge",
    ("amused", "yesbet"):      "Yesbet",

    # ── Betmakers technology (platform=betmakers, plus Booki family) ───────
    ("betmakers", "baggybet"):   "Baggybet",
    ("betmakers", "betdash"):    "Betdash",
    ("betmakers", "betestate"):  "Betestate",
    ("betmakers", "betit"):      "Betit",
    ("betmakers", "betlegends"): "Betlegends",
    ("betmakers", "betlocal"):   "Betlocal",
    ("betmakers", "crossbet"):   "Crossbet",
    ("betmakers", "crownbet"):   "Crownbet",
    ("betmakers", "diamondbet"): "Diamondbet",
    ("betmakers", "earlycrow"):  "Earlycrow",
    ("betmakers", "fatbet"):     "Fatbet",
    ("betmakers", "knucklebet"): "Knucklebet",
    ("betmakers", "multis"):     "Multis",
    ("betmakers", "next2go"):    "Next2go",
    ("betmakers", "okebet"):     "OkeBet",
    ("betmakers", "ponybet"):    "Ponybet",
    ("betmakers", "realbookie"): "Realbookie",
    ("betmakers", "swiftbet"):   "Swiftbet",
    ("betmakers", "terrybet"):   "Terrybet",

    # Also accept Booki family stored under platform="booki"
    ("booki", "betit"):      "Betit",
    ("booki", "earlycrow"):  "Earlycrow",
    ("booki", "knucklebet"): "Knucklebet",
    ("booki", "ponybet"):    "Ponybet",

    # ── Entain family ──────────────────────────────────────────────────────
    ("entain", "neds"):      "Neds",
    ("entain", "ladbrokes"): "Ladbrokes",
    ("neds", ""):            "Neds",
    ("ladbrokes", ""):       "Ladbrokes",

    # ── Punterstech ────────────────────────────────────────────────────────
    ("punterstech", "blondebet"): "Blondebet",
    ("punterstech", "goldbet"):   "Goldbet",
    ("punterstech", "mintbet"):   "Mintbet",
    ("blondebet", ""):            "Blondebet",
    ("goldbet", ""):              "Goldbet",
    ("mintbet", ""):              "Mintbet",

    # ── Betcloud ───────────────────────────────────────────────────────────
    ("betcloud", "multis"): "Multis",

    # ── Genweb ─────────────────────────────────────────────────────────────
    ("genweb", "elitebet"): "Elitebet",
    ("elitebet", ""):       "Elitebet",
}


def _load_env_overrides():
    """Merge BETOPS_BOOKMAKER_MAP env JSON over the defaults, one-shot at import."""
    raw = os.environ.get("BETOPS_BOOKMAKER_MAP", "").strip()
    if not raw:
        return
    try:
        overrides = json.loads(raw)
        for key, value in overrides.items():
            if "/" in key:
                platform, brand = key.split("/", 1)
            else:
                platform, brand = key, ""
            BOOKMAKER_MAP[(platform.lower(), brand.lower())] = value
        logger.info("BetOps: applied %d bookmaker mapping override(s) from env", len(overrides))
    except Exception as e:
        logger.error("BetOps: invalid BETOPS_BOOKMAKER_MAP env JSON: %s", e)


_load_env_overrides()


def resolve_bookmaker(platform: str, brand: str = "") -> Optional[str]:
    """Return the BetOps bookmaker string for (platform, brand), or None if unmapped."""
    if not platform:
        return None
    p = platform.lower().strip()
    b = (brand or "").lower().strip()
    # Try most-specific then fall back to platform-only
    return BOOKMAKER_MAP.get((p, b)) or BOOKMAKER_MAP.get((p, ""))


# ─── Outbox schema ─────────────────────────────────────────────────────────────

async def init_outbox(pool) -> None:
    """Create the retry outbox + add betops_bet_id columns. Idempotent.

    The outbox has a `kind` column so Create and Grade operations share the
    retry machinery: payload format and endpoint differ, kind picks which.
    betops_bet_id columns on the bet tables store the ID returned by BetOps's
    Create response so we can later target PUT/POST /api/bets/{betId}/result.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS betops_outbox (
                bet_id TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'create',
                username TEXT NOT NULL,
                payload JSONB NOT NULL,
                attempts INT NOT NULL DEFAULT 0,
                last_error TEXT,
                next_retry_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                delivered_at TIMESTAMPTZ,
                permanent_failure BOOLEAN NOT NULL DEFAULT FALSE,
                PRIMARY KEY (bet_id, kind)
            );
            """
        )
        # Legacy rows (before the kind split) won't have a kind value; default keeps them valid.
        await conn.execute(
            "ALTER TABLE betops_outbox ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'create';"
        )
        # Upgrade the primary key from (bet_id) → (bet_id, kind) if the old PK
        # is still in place. Needed so a Create and a Grade for the same bet
        # can coexist in the outbox as separate rows.
        pk_cols_row = await conn.fetchrow(
            """
            SELECT string_agg(a.attname, ',' ORDER BY array_position(i.indkey::int[], a.attnum)) AS cols
            FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = 'betops_outbox'::regclass AND i.indisprimary;
            """
        )
        current_pk = (pk_cols_row["cols"] if pk_cols_row else "") or ""
        if current_pk == "bet_id":
            try:
                await conn.execute("ALTER TABLE betops_outbox DROP CONSTRAINT betops_outbox_pkey;")
                await conn.execute("ALTER TABLE betops_outbox ADD PRIMARY KEY (bet_id, kind);")
                logger.info("BetOps: upgraded betops_outbox PK to (bet_id, kind)")
            except Exception as _e:
                logger.warning("BetOps: failed to upgrade outbox PK: %s", _e)
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_betops_outbox_pending
            ON betops_outbox(next_retry_at)
            WHERE delivered_at IS NULL AND permanent_failure = FALSE;
            """
        )
        # Store BetOps' returned bet_id on each local bet row so grading knows the URL target.
        for sql in (
            "ALTER TABLE bets ADD COLUMN IF NOT EXISTS betops_bet_id TEXT;",
            "ALTER TABLE multi_bets ADD COLUMN IF NOT EXISTS betops_bet_id TEXT;",
            "ALTER TABLE bet365_picks ADD COLUMN IF NOT EXISTS betops_bet_id TEXT;",
        ):
            try:
                await conn.execute(sql)
            except Exception as _e:
                logger.warning("BetOps init_outbox ALTER skipped: %s (%s)", sql, _e)


# ─── Payload construction ──────────────────────────────────────────────────────

def _num(v, default: float = 0.0) -> float:
    """Parse a possibly-stringy dollar/odds value."""
    try:
        return float(str(v).replace("$", "").replace(",", "").strip())
    except Exception:
        return default


def _format_event(game_id: str, bet_type: str, legs: list) -> str:
    """Derive a human-readable event label from game_id and/or legs.

    SGM: game_id "20260420_PHX_OKC" → "PHX vs OKC".
    Multi (cross-game): pull team tags from each leg and join.
    Falls back to the raw game_id if nothing else works.
    """
    if game_id and "_" in game_id:
        parts = game_id.split("_")
        if len(parts) >= 3:
            return f"{parts[1]} vs {parts[2]}"

    bt = (bet_type or "").upper()
    if bt in ("MULTI", "MULTI_GAME") or not game_id:
        games = []
        for leg in legs or []:
            name = leg.get("name", "") if isinstance(leg, dict) else str(leg)
            m = re.search(r"\b([A-Z]{2,3})-([A-Za-z]{2,4})\b", name)
            if m:
                tag = f"{m.group(1).upper()}-{m.group(2).upper()}"
                if tag not in games:
                    games.append(tag)
        if games:
            return "Multi: " + " + ".join(games)

    return game_id or "Multi"


def _activity_from_source(source: Optional[str]) -> str:
    if not source:
        return "non_promo"
    return "promo" if source.lower() in PROMO_SOURCE_TAGS else "non_promo"


def build_payload(bet: dict, account_email: str, bookmaker: str = DEFAULT_BOOKMAKER) -> dict:
    """Build a BetOps Create Bet payload from a BotOps bet dict."""
    legs = bet.get("legs") or []
    selection_parts = []
    for leg in legs:
        if isinstance(leg, dict):
            name = leg.get("name") or leg.get("selectionName") or ""
        else:
            name = str(leg)
        if name:
            selection_parts.append(name)
    selection = " / ".join(selection_parts) or "Unknown"

    bt_local = (bet.get("bet_type") or "SGM").upper()
    event = _format_event(bet.get("game_id") or "", bt_local, legs)

    placed_at = bet.get("placed_at")
    if isinstance(placed_at, datetime):
        placed_iso = placed_at.astimezone(timezone.utc).isoformat()
    elif isinstance(placed_at, str) and placed_at:
        placed_iso = placed_at
    else:
        placed_iso = datetime.now(timezone.utc).isoformat()

    notes_bits = []
    if bet.get("id"):
        notes_bits.append(f"bet_id={bet['id']}")
    if bet.get("tsn"):
        notes_bits.append(f"tsn={bet['tsn']}")
    if bet.get("account_label"):
        notes_bits.append(f"acct={bet['account_label']}")

    return {
        "email": account_email,
        "bookmaker": bookmaker,
        "activity": _activity_from_source(bet.get("source")),
        "bet_type": "sport",  # All TAB bets routed through save_bet are sport markets.
        "event": event,
        "selection": selection,
        "odds": _num(bet.get("combined_odds")),
        "stake": _num(bet.get("stake")),
        "placed_at": placed_iso,
        "tipper": bet.get("source") or "botops",
        "notes": " ".join(notes_bits) if notes_bits else None,
        # Our local bet UUID. BetOps uses this for idempotent dedupe — any retry
        # that reaches BetOps after a prior success returns the existing row with
        # deduplicated: true instead of creating a new record.
        "external_id": bet.get("id"),
    }


# ─── Account email lookup ──────────────────────────────────────────────────────

async def _lookup_email(username: str, account_number: str, pool) -> Optional[str]:
    """Resolve the BetOps-matching email for a given TAB account_number.

    Checks tab_sessions first (fast path — always populated during login) and
    falls back to bookie_accounts so non-TAB platforms still work once wired.
    """
    if not account_number:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT email FROM tab_sessions WHERE account_number = $1 ORDER BY logged_in_at DESC LIMIT 1",
            account_number,
        )
        if row and row["email"]:
            return row["email"]
        row = await conn.fetchrow(
            "SELECT email FROM bookie_accounts WHERE account_number = $1 AND username = $2 LIMIT 1",
            account_number, username,
        )
        if row and row["email"]:
            return row["email"]
    return None


# ─── Emission + retry ──────────────────────────────────────────────────────────

def _is_permanent_failure(status_code: int) -> bool:
    """BetOps returns 400 for invalid fields and 404 when the bookmaker or email
    account isn't registered. Retrying those forever never helps — mark permanent
    so an operator can fix (e.g. register the account on BetOps side)."""
    return status_code in (400, 404, 422)


async def _enqueue_outbox(
    pool, bet_id: str, username: str, payload: dict,
    error: str, permanent: bool, initial: bool = True,
    kind: str = "create",
) -> None:
    """UPSERT into the outbox. Keyed by (bet_id, kind) so a Create and a Grade
    for the same bet can coexist without clobbering each other."""
    next_retry = datetime.now(timezone.utc) + timedelta(minutes=1)
    async with pool.acquire() as conn:
        if initial:
            await conn.execute(
                """
                INSERT INTO betops_outbox (bet_id, kind, username, payload, attempts,
                                           last_error, next_retry_at, permanent_failure)
                VALUES ($1, $2, $3, $4::jsonb, 1, $5, $6, $7)
                ON CONFLICT (bet_id, kind) DO UPDATE SET
                    attempts = betops_outbox.attempts + 1,
                    last_error = EXCLUDED.last_error,
                    next_retry_at = EXCLUDED.next_retry_at,
                    permanent_failure = EXCLUDED.permanent_failure;
                """,
                bet_id, kind, username, json.dumps(payload), error, next_retry, permanent,
            )


async def _update_outbox_retry(
    pool, bet_id: str, error: str, delay_min: int, permanent: bool,
    kind: str = "create",
) -> None:
    next_retry = datetime.now(timezone.utc) + timedelta(minutes=delay_min)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE betops_outbox
            SET attempts = attempts + 1,
                last_error = $2,
                next_retry_at = $3,
                permanent_failure = $4
            WHERE bet_id = $1 AND kind = $5
            """,
            bet_id, error, next_retry, permanent, kind,
        )


# ─── Grading ───────────────────────────────────────────────────────────────────

# Translate BotOps' internal status vocabulary to the values BetOps' grading
# endpoint accepts. Anything not in this map means "don't grade yet".
_STATUS_MAP_TO_BETOPS = {
    "won": "won",
    "lost": "lost",
    "refunded": "void",
    "void": "void",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "pushed": "void",        # BetOps internally maps pushed → void per Diji
    "push": "void",
    "cashed out": "cashout",
    "cashedout": "cashout",
}


def _map_status_for_grading(local_status: str) -> Optional[str]:
    s = (local_status or "").strip().lower()
    return _STATUS_MAP_TO_BETOPS.get(s)


async def _get_betops_bet_id(pool, local_table: str, local_bet_id: str) -> Optional[str]:
    if local_table not in _LOCAL_TABLES:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT betops_bet_id FROM {local_table} WHERE id = $1",
            local_bet_id,
        )
        return row["betops_bet_id"] if row and row["betops_bet_id"] else None


async def emit_grade_to_betops(
    local_bet_id: str, local_status: str, payout, pool,
    local_table: str = "bets", username: str = "",
    settled_at: Optional[datetime] = None,
    bonus_amount: Optional[float] = None,
) -> None:
    """Forward a bet outcome to BetOps's grading endpoint.

    No-op for statuses that don't map (Pending, Placed, etc). Fire-and-forget
    with outbox retry — same guarantees as Create."""
    mapped = _map_status_for_grading(local_status)
    if not mapped:
        return

    try:
        bo_id = await _get_betops_bet_id(pool, local_table, local_bet_id)
        if not bo_id:
            # The Create POST never landed successfully (or is still queued).
            # Enqueue a grading task anyway so the flush loop picks it up once
            # Create eventually succeeds — but flag as non-permanent.
            payload = {
                "_pending_betops_id": True,
                "local_bet_id": local_bet_id,
                "local_table": local_table,
                "status": mapped,
                "payout": _num(payout),
                "settled_at": (settled_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
                "bonus_amount": _num(bonus_amount) if bonus_amount is not None else None,
            }
            await _enqueue_outbox(
                pool, local_bet_id, username or "botops", payload,
                error="no betops_bet_id yet (Create not confirmed)", permanent=False,
                kind="grade",
            )
            logger.info("BetOps grade: deferred bet_id=%s (no betops_bet_id yet)", local_bet_id)
            return

        body = {
            "status": mapped,
            "payout": _num(payout),
            "settled_at": (settled_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
        }
        if bonus_amount is not None:
            body["bonus_amount"] = _num(bonus_amount)

        url = f"{BETOPS_URL.rstrip('/')}/{bo_id}/result"
        try:
            async with httpx.AsyncClient(timeout=BETOPS_TIMEOUT) as client:
                resp = await client.post(url, json=body)
                if resp.status_code < 400 or resp.status_code == 409:
                    # 409 = bet already resulted; Diji confirmed idempotent-safe.
                    logger.info(
                        "BetOps grade: POST bet_id=%s status=%d (%s)",
                        local_bet_id, resp.status_code, mapped,
                    )
                    return
                err = f"{resp.status_code}: {resp.text[:200]}"
                permanent = _is_permanent_failure(resp.status_code) and resp.status_code != 409
                payload = {"url": url, **body}
                await _enqueue_outbox(
                    pool, local_bet_id, username or "botops", payload,
                    error=err, permanent=permanent, kind="grade",
                )
                logger.warning("BetOps grade: POST failed bet_id=%s %s", local_bet_id, err)
        except Exception as e:
            payload = {"url": url, **body}
            await _enqueue_outbox(
                pool, local_bet_id, username or "botops", payload,
                error=f"exception: {e}", permanent=False, kind="grade",
            )
            logger.warning("BetOps grade: POST exception bet_id=%s: %s", local_bet_id, e)
    except Exception as e:
        logger.error("BetOps emit_grade unexpected error bet_id=%s: %s", local_bet_id, e, exc_info=True)


async def _post_or_enqueue(
    bet_id: str, username: str, payload: dict, pool,
    local_table: Optional[str] = None,
) -> Optional[dict]:
    """Centralized POST attempt — every flavor of emit converges here.
    Success → parses BetOps response (extracts betId + deduplicated flag) and,
    if `local_table` is set, writes betops_bet_id back onto that row.
    Failure → enqueues to outbox for retry.

    Returns the parsed response dict on success, None on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=BETOPS_TIMEOUT) as client:
            resp = await client.post(BETOPS_URL, json=payload)
            if resp.status_code < 400:
                parsed = None
                try:
                    parsed = resp.json()
                except Exception:
                    parsed = {}
                bo_id = parsed.get("betId") or parsed.get("bet_id") or ""
                dedup = bool(parsed.get("deduplicated"))
                logger.info(
                    "BetOps: POST success bet_id=%s status=%d betops_id=%s%s",
                    bet_id, resp.status_code, bo_id, " (dedup)" if dedup else "",
                )
                if bo_id and local_table:
                    await _persist_betops_id(pool, local_table, bet_id, bo_id)
                return parsed
            err = f"{resp.status_code}: {resp.text[:200]}"
            permanent = _is_permanent_failure(resp.status_code)
            await _enqueue_outbox(pool, bet_id, username, payload, error=err, permanent=permanent)
            logger.warning("BetOps: POST failed bet_id=%s %s (permanent=%s)", bet_id, err, permanent)
    except Exception as e:
        await _enqueue_outbox(
            pool, bet_id, username, payload,
            error=f"exception: {e}", permanent=False,
        )
        logger.warning("BetOps: POST exception bet_id=%s: %s", bet_id, e)
    return None


# Columns allowed for the betops_bet_id write-back. Hardcoded so we never
# interpolate a caller-controlled string into SQL.
_LOCAL_TABLES = {"bets", "multi_bets", "bet365_picks"}


async def _persist_betops_id(pool, table: str, local_bet_id: str, betops_bet_id: str) -> None:
    if table not in _LOCAL_TABLES:
        logger.warning("BetOps: refusing to persist betops_bet_id to unknown table %s", table)
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                f"UPDATE {table} SET betops_bet_id = $1 WHERE id = $2 AND (betops_bet_id IS NULL OR betops_bet_id = '')",
                betops_bet_id, local_bet_id,
            )
    except Exception as e:
        logger.warning("BetOps: failed to persist betops_bet_id on %s.%s: %s", table, local_bet_id, e)


async def emit_to_betops(bet: dict, username: str, pool) -> None:
    """TAB flavor — called from database.save_bet. Fire-and-forget, never raises."""
    bet_id = bet.get("id") or ""
    try:
        account_email = await _lookup_email(username, bet.get("account_number", ""), pool)
        if not account_email:
            logger.warning(
                "BetOps: no email resolved for account %s; enqueuing as permanent",
                bet.get("account_number"),
            )
            payload = build_payload(bet, account_email="", bookmaker=DEFAULT_BOOKMAKER)
            await _enqueue_outbox(
                pool, bet_id, username, payload,
                error="no email for account", permanent=True,
            )
            return
        payload = build_payload(bet, account_email=account_email, bookmaker=DEFAULT_BOOKMAKER)
        await _post_or_enqueue(bet_id, username, payload, pool, local_table="bets")
    except Exception as e:
        logger.error("BetOps emit_to_betops unexpected error bet_id=%s: %s", bet_id, e, exc_info=True)


async def _lookup_email_bookie(
    username: str, platform: str, brand: str, initials: str, pool,
) -> Optional[str]:
    """Email lookup for non-TAB platforms via bookie_accounts."""
    async with pool.acquire() as conn:
        # Try (platform, brand, initials) first; fall back progressively.
        row = await conn.fetchrow(
            """SELECT email FROM bookie_accounts
               WHERE username = $1 AND LOWER(platform) = LOWER($2)
                 AND LOWER(COALESCE(brand,'')) = LOWER(COALESCE($3,''))
                 AND COALESCE(initials,'') = COALESCE($4,'')
               LIMIT 1""",
            username, platform, brand, initials,
        )
        if row and row["email"]:
            return row["email"]
        row = await conn.fetchrow(
            """SELECT email FROM bookie_accounts
               WHERE username = $1 AND LOWER(platform) = LOWER($2)
                 AND LOWER(COALESCE(brand,'')) = LOWER(COALESCE($3,''))
               LIMIT 1""",
            username, platform, brand,
        )
        if row and row["email"]:
            return row["email"]
        row = await conn.fetchrow(
            """SELECT email FROM bookie_accounts
               WHERE username = $1 AND LOWER(platform) = LOWER($2)
               LIMIT 1""",
            username, platform,
        )
        if row and row["email"]:
            return row["email"]
    return None


def _racing_activity(stake_type: Optional[str]) -> str:
    """Map multi_bets.stake_type to BetOps activity.

    cash/mug → non_promo, promo → promo. bonus intentionally mapped to non_promo
    for now per operator direction (bonus-bet detection deferred)."""
    s = (stake_type or "").lower()
    if s == "promo":
        return "promo"
    return "non_promo"


async def emit_racing_bet_to_betops(mb: dict, username: str, pool) -> None:
    """Racing/multi-bookie flavor — called from multi_database.save_multi_bet."""
    bet_id = mb.get("id") or ""
    try:
        platform = mb.get("platform", "")
        brand = mb.get("brand", "")
        bookmaker = resolve_bookmaker(platform, brand)
        account_email = await _lookup_email_bookie(
            username, platform, brand, mb.get("initials", ""), pool,
        )

        # Event: "Track R{race}" matches the spec example ("Ascot R5"). Fall back
        # gracefully if pieces are missing so the operator still sees SOMETHING.
        track = (mb.get("track") or "").strip()
        race_num = mb.get("race_number")
        if track and race_num:
            event = f"{track} R{race_num}"
        elif track:
            event = track
        else:
            event = "Racing"

        selection = (mb.get("horse") or "").strip() or "Unknown"

        payload = {
            "email": account_email or "",
            "bookmaker": bookmaker or platform,  # fall back to raw platform if unmapped
            "activity": _racing_activity(mb.get("stake_type")),
            "bet_type": "racing",
            "event": event,
            "selection": selection,
            "odds": _num(mb.get("odds")),
            "stake": _num(mb.get("stake")),
            "placed_at": datetime.now(timezone.utc).isoformat(),
            "race": f"R{race_num}" if race_num else None,
            "tipper": mb.get("method") or "botops",
            "notes": f"bet_id={bet_id} ref={mb.get('bet_reference','')} initials={mb.get('initials','')}".strip(),
            "external_id": bet_id,
        }

        # Enqueue immediately as permanent failure if we're missing the two
        # fields BetOps requires to match the account.
        if not account_email:
            logger.warning("BetOps (racing): no email for %s/%s/%s; permanent outbox", platform, brand, mb.get("initials"))
            await _enqueue_outbox(pool, bet_id, username, payload,
                                  error="no email for racing account", permanent=True)
            return
        if not bookmaker:
            logger.warning("BetOps (racing): no bookmaker mapping for (%s, %s); permanent outbox", platform, brand)
            await _enqueue_outbox(pool, bet_id, username, payload,
                                  error=f"unmapped bookmaker: {platform}/{brand}", permanent=True)
            return

        await _post_or_enqueue(bet_id, username, payload, pool, local_table="multi_bets")
    except Exception as e:
        logger.error("BetOps emit_racing_bet_to_betops unexpected error bet_id=%s: %s", bet_id, e, exc_info=True)


async def emit_bet365_pick_to_betops(pick: dict, username: str, pool) -> None:
    """bet365 flavor — called from bet365_routes.save_pick_to_db ONLY for placed picks.

    Skipped picks (pipeline filtered them out before placement) don't belong in
    BetOps — they're not real bets. The caller should gate on pick['placed']."""
    bet_id = pick.get("id") or ""
    try:
        if not pick.get("placed"):
            return  # defensive: only emit actually-placed picks

        bookmaker = resolve_bookmaker("bet365")
        # bet365 in this repo doesn't track per-pick account_number; the Megaboost
        # flow uses a single logged-in bet365 account per browser session. Resolve
        # via the bookie_accounts table on (platform=bet365) for this username.
        account_email = await _lookup_email_bookie(username, "bet365", "bet365", "", pool)

        # Activity: Megaboost is promo, everything else non_promo.
        source = (pick.get("source") or "").lower()
        activity = "promo" if "megaboost" in source or "mega" in source else "non_promo"

        # Event & selection: bet365 picks are single-leg (player stat N+ / side)
        player = pick.get("player") or ""
        stat = pick.get("stat") or ""
        side = pick.get("side") or ""
        line = pick.get("line")
        sport = (pick.get("sport") or "").strip() or "Unknown"
        event = sport
        selection = f"{player} {side} {line} {stat}".strip() if player else sport

        payload = {
            "email": account_email or "",
            "bookmaker": bookmaker or "Bet365",
            "activity": activity,
            "bet_type": "sport",
            "event": event,
            "selection": selection,
            "odds": _num(pick.get("actual_odds") or pick.get("odds")),
            "stake": _num(pick.get("stake")),
            "placed_at": datetime.now(timezone.utc).isoformat(),
            "tipper": source or "bet365",
            "notes": f"bet_id={bet_id} source={source}".strip(),
            "external_id": bet_id,
        }

        if not account_email:
            logger.warning("BetOps (bet365): no email for bet365 account; permanent outbox")
            await _enqueue_outbox(pool, bet_id, username, payload,
                                  error="no email for bet365 account", permanent=True)
            return

        await _post_or_enqueue(bet_id, username, payload, pool, local_table="bet365_picks")
    except Exception as e:
        logger.error("BetOps emit_bet365_pick_to_betops unexpected error bet_id=%s: %s", bet_id, e, exc_info=True)


async def flush_outbox_loop(pool, poll_interval_seconds: int = 60) -> None:
    """Background task: retries pending outbox entries on a fixed poll interval.

    Handles both 'create' (POST /api/bets) and 'grade' (POST /api/bets/{id}/result)
    rows. Exponential backoff per entry: 1m → 5m → 25m → 2h → 4h (capped).
    """
    logger.info("BetOps outbox flush loop started (interval=%ds)", poll_interval_seconds)
    while True:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT bet_id, kind, username, payload, attempts
                    FROM betops_outbox
                    WHERE delivered_at IS NULL
                      AND permanent_failure = FALSE
                      AND next_retry_at <= NOW()
                    ORDER BY next_retry_at
                    LIMIT 20
                    """
                )

            for row in rows:
                bet_id = row["bet_id"]
                kind = row["kind"] or "create"
                attempts = row["attempts"]
                raw_payload = row["payload"]
                payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload

                try:
                    if kind == "grade":
                        await _flush_grade_row(pool, bet_id, row["username"], payload, attempts)
                    else:
                        await _flush_create_row(pool, bet_id, payload, attempts)
                except Exception as e:
                    delay_min = min(240, max(1, 5 ** min(attempts, 4)))
                    await _update_outbox_retry(pool, bet_id, f"exception: {e}", delay_min, False, kind=kind)
        except Exception as e:
            logger.error("BetOps flush loop iteration error: %s", e, exc_info=True)

        await asyncio.sleep(poll_interval_seconds)


async def _flush_create_row(pool, bet_id: str, payload: dict, attempts: int) -> None:
    async with httpx.AsyncClient(timeout=BETOPS_TIMEOUT) as client:
        resp = await client.post(BETOPS_URL, json=payload)
        if resp.status_code < 400:
            try:
                parsed = resp.json()
                bo_id = parsed.get("betId") or parsed.get("bet_id") or ""
            except Exception:
                bo_id = ""
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE betops_outbox SET delivered_at = NOW() WHERE bet_id = $1 AND kind = 'create'",
                    bet_id,
                )
            # Best-effort backfill of betops_bet_id across the three tables. We
            # don't know which one owns this bet_id, so try each in order.
            if bo_id:
                for tbl in ("bets", "multi_bets", "bet365_picks"):
                    await _persist_betops_id(pool, tbl, bet_id, bo_id)
            logger.info(
                "BetOps outbox (create): delivered bet_id=%s attempt=%d betops_id=%s",
                bet_id, attempts + 1, bo_id,
            )
            return
        err = f"{resp.status_code}: {resp.text[:200]}"
        permanent = _is_permanent_failure(resp.status_code)
        delay_min = min(240, max(1, 5 ** min(attempts, 4)))
        await _update_outbox_retry(pool, bet_id, err, delay_min, permanent, kind="create")


async def _flush_grade_row(pool, bet_id: str, username: str, payload: dict, attempts: int) -> None:
    """Retry a grading POST. If the payload is flagged _pending_betops_id it
    means Create hadn't landed when we first tried to grade — resolve the real
    betops_bet_id from the local table now and promote the payload."""
    if payload.get("_pending_betops_id"):
        local_table = payload.get("local_table") or "bets"
        local_bet_id = payload.get("local_bet_id") or bet_id
        bo_id = await _get_betops_bet_id(pool, local_table, local_bet_id)
        if not bo_id:
            # Still no Create ack — keep deferring (reset attempts so we don't
            # hit the cap prematurely; the real failure here is upstream).
            delay_min = 5
            await _update_outbox_retry(
                pool, bet_id, "still waiting for Create bet_id",
                delay_min, False, kind="grade",
            )
            return
        resolved_url = f"{BETOPS_URL.rstrip('/')}/{bo_id}/result"
        resolved_body = {k: v for k, v in payload.items()
                         if k not in ("_pending_betops_id", "local_bet_id", "local_table")}
        payload = {"url": resolved_url, **resolved_body}
        # Persist the resolved form so a later flush doesn't re-do the lookup.
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE betops_outbox SET payload = $2::jsonb WHERE bet_id = $1 AND kind = 'grade'",
                bet_id, json.dumps(payload),
            )

    url = payload.get("url")
    body = {k: v for k, v in payload.items() if k != "url"}
    if not url:
        await _update_outbox_retry(
            pool, bet_id, "grade payload missing url", 60, True, kind="grade",
        )
        return

    async with httpx.AsyncClient(timeout=BETOPS_TIMEOUT) as client:
        resp = await client.post(url, json=body)
        if resp.status_code < 400 or resp.status_code == 409:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE betops_outbox SET delivered_at = NOW() WHERE bet_id = $1 AND kind = 'grade'",
                    bet_id,
                )
            logger.info(
                "BetOps outbox (grade): delivered bet_id=%s attempt=%d status=%d",
                bet_id, attempts + 1, resp.status_code,
            )
            return
        err = f"{resp.status_code}: {resp.text[:200]}"
        permanent = _is_permanent_failure(resp.status_code) and resp.status_code != 409
        delay_min = min(240, max(1, 5 ** min(attempts, 4)))
        await _update_outbox_retry(pool, bet_id, err, delay_min, permanent, kind="grade")

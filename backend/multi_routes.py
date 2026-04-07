"""FastAPI routes for multi-bookie operations: accounts, CSV upload, batch execution."""
import asyncio
import logging
import uuid

from fastapi import APIRouter, HTTPException, Depends, Header

from pydantic import BaseModel
from multi_models import BookieAccountCreate, CsvUploadResponse, BatchStatusResponse


class CsvUploadRequest(BaseModel):
    csv_text: str
from multi_database import (
    get_bookie_accounts, upsert_bookie_account, delete_bookie_account,
    find_account_by_id, create_csv_batch, insert_csv_rows, get_batch_status,
    get_recent_batches, get_multi_bets, get_multi_bet_stats,
)
from csv_processor import parse_racing_csv, validate_csv_rows, process_batch
from database import get_app_session

logger = logging.getLogger(__name__)

multi_router = APIRouter(prefix="/api/multi", tags=["multi-bookie"])


# ─── Auth dependency (mirrors main.py pattern) ─────────────────────────────

async def _verify_app_token(authorization: str = Header(None)) -> dict:
    """Verify app auth token from Authorization header.
    Same logic as main.py — checks in-memory dict first, then DB."""
    if not authorization:
        raise HTTPException(401, "Missing authorization. Please login to the app first.")
    token = authorization.replace("Bearer ", "").replace("bearer ", "")

    # Import app_tokens from main at call time to avoid circular import at module level
    from main import app_tokens

    if token in app_tokens:
        return app_tokens[token]
    # Check DB
    session = await get_app_session(token)
    if session:
        app_tokens[token] = {
            "username": session["username"],
            "name": session["name"],
            "created_at": session["created_at"],
        }
        return app_tokens[token]
    raise HTTPException(401, "Invalid or expired app token. Please login again.")


# ─── Bookie Accounts ───────────────────────────────────────────────────────

@multi_router.get("/accounts")
async def api_list_bookie_accounts(user: dict = Depends(_verify_app_token)):
    """List all bookie accounts for the current user."""
    accounts = await get_bookie_accounts(user["username"])
    return {"accounts": accounts}


@multi_router.post("/accounts")
async def api_upsert_bookie_account(
    body: BookieAccountCreate, user: dict = Depends(_verify_app_token)
):
    """Create or update a bookie account."""
    data = body.model_dump()
    data["username"] = user["username"]
    data["initials"] = data["initials"].upper()
    data["brand"] = data["brand"].lower()
    data["platform"] = data["platform"].lower()
    result = await upsert_bookie_account(data)
    return {"ok": True, "id": result["id"]}


@multi_router.put("/accounts/{account_id}")
async def api_update_bookie_account(
    account_id: str, body: BookieAccountCreate, user: dict = Depends(_verify_app_token)
):
    """Update an existing bookie account."""
    data = body.model_dump()
    data["id"] = account_id
    data["username"] = user["username"]
    data["initials"] = data["initials"].upper()
    data["brand"] = data["brand"].lower()
    data["platform"] = data["platform"].lower()
    # Don't overwrite password if blank (edit mode)
    if not data.get("password"):
        existing = await find_account_by_id(account_id)
        if existing:
            data["password"] = existing["password"]
    result = await upsert_bookie_account(data)
    return {"ok": True, "id": result["id"]}


@multi_router.delete("/accounts/{account_id}")
async def api_delete_bookie_account(
    account_id: str, user: dict = Depends(_verify_app_token)
):
    """Delete a bookie account."""
    await delete_bookie_account(account_id)
    return {"ok": True}


@multi_router.post("/accounts/{account_id}/test-login")
async def api_test_login(account_id: str, user: dict = Depends(_verify_app_token)):
    """Test login for a bookie account. Returns balance on success."""
    from multi_database import find_account_by_id
    account = await find_account_by_id(account_id)
    if not account:
        raise HTTPException(404, "Account not found")

    from platforms.registry import get_client, normalize_bookmaker
    import random

    brand = normalize_bookmaker(account["brand"])
    platform = account["platform"]
    client = get_client(platform)
    if not client:
        raise HTTPException(400, f"Unsupported platform: {platform}")

    # Build brand config from the platform's brand registry
    brand_config = {}
    if platform == "betmakers":
        from platforms.betmakers import BETMAKERS_BRANDS
        brand_config = BETMAKERS_BRANDS.get(brand, {}) or {}
    elif platform == "amused":
        from platforms.amused import AMUSED_BRANDS
        brand_config = AMUSED_BRANDS.get(brand, {}) or {}

    # Generate proxy URL
    proxy_base = account.get("proxy_base", "")
    if proxy_base:
        # If proxy_base is already a full URL (http://user:pass@host:port), use as-is
        if "://" in proxy_base and "@" in proxy_base:
            proxy_url = proxy_base
        else:
            # Oxylabs format: proxy_base is the username prefix
            sess_id = str(random.randint(1000000000, 9999999999))
            proxy_url = f"{proxy_base}-sessid-{sess_id}-sesstime-10:K5E=2qcyhfyFZs~@pr.oxylabs.io:7777"
    else:
        proxy_url = ""

    try:
        session = await client.login(
            email=account["email"],
            password=account["password"],
            proxy_url=proxy_url,
            brand_config=brand_config,
        )
        if not session.get("success"):
            raise HTTPException(400, f"Login failed: {session.get('error', 'Unknown error')}")

        balances = await client.get_balances(session)
        balance = balances.get("cash", 0.0)
        bonus = balances.get("bonus", 0.0)

        return {"ok": True, "balance": balance, "bonus": bonus, "message": f"Login OK. Balance: ${balance:.2f}, Bonus: ${bonus:.2f}"}
    except Exception as e:
        logger.error(f"Test login failed for {account_id}: {e}")
        raise HTTPException(400, f"Login failed: {str(e)}")


# ─── Promo Scanner ────────────────────────────────────────────────────────


class PromoScanRequest(BaseModel):
    brands: list[str] | None = None  # filter to specific brands, or None for all


@multi_router.post("/scan-promos")
async def api_scan_promos(
    body: PromoScanRequest = None,
    user: dict = Depends(_verify_app_token),
):
    """Scan bookie accounts for tokens/bonus balances and available promotions.
    Returns SSE stream with real-time progress per account."""
    import json as _json
    from fastapi.responses import StreamingResponse
    from platforms.registry import get_client, normalize_bookmaker
    from platforms.betmakers import BETMAKERS_BRANDS, BetMakersClient
    import random

    accounts = await get_bookie_accounts(user["username"])

    brand_filter = None
    if body and body.brands:
        brand_filter = {b.lower().strip() for b in body.brands}

    # Platforms that don't support promo scanning yet
    # bet365: fully browser-automated, no API for promos
    SKIP_PLATFORMS = {"bet365"}

    # Filter accounts
    filtered = []
    for acct in accounts:
        brand = normalize_bookmaker(acct["brand"])
        if brand_filter and brand not in brand_filter:
            continue
        if acct["platform"] in SKIP_PLATFORMS:
            continue
        filtered.append(acct)

    async def stream():
        results = []
        seen_brands = set()
        total = len(filtered)

        # Send initial count
        yield f"data: {_json.dumps({'type': 'start', 'total': total})}\n\n"

        for idx, acct in enumerate(filtered):
            brand = normalize_bookmaker(acct["brand"])
            platform = acct["platform"]
            seen_brands.add((platform, brand))

            # Send "scanning" event
            yield f"data: {_json.dumps({'type': 'scanning', 'index': idx, 'total': total, 'brand': acct['brand'], 'initials': acct['initials'], 'platform': platform})}\n\n"

            # Build brand config
            brand_config = {}
            if platform == "betmakers":
                brand_config = BETMAKERS_BRANDS.get(brand, {}) or {}
            elif platform == "amused":
                from platforms.amused import AMUSED_BRANDS
                brand_config = AMUSED_BRANDS.get(brand, {}) or {}

            # Generate proxy URL
            proxy_base = acct.get("proxy_base", "")
            if proxy_base:
                # Strip http:// prefix if it's just an Oxylabs username (no @ = not a full URL)
                cleaned = proxy_base
                if "://" in cleaned and "@" not in cleaned:
                    cleaned = cleaned.split("://", 1)[1]
                if "@" in cleaned:
                    # Full proxy URL (user:pass@host:port)
                    proxy_url = proxy_base
                else:
                    # Oxylabs username prefix
                    sess_id = str(random.randint(1000000000, 9999999999))
                    sess_time = 120 if platform == "sportsbet" else 10
                    proxy_url = f"http://{cleaned}-sessid-{sess_id}-sesstime-{sess_time}:K5E=2qcyhfyFZs~@pr.oxylabs.io:7777"
            else:
                proxy_url = ""

            client = get_client(platform)
            entry = {
                "account_id": acct["id"],
                "brand": acct["brand"],
                "platform": platform,
                "initials": acct["initials"],
                "email": acct["email"],
                "cash": 0.0,
                "bonus": 0.0,
                "status": "pending",
                "error": None,
            }

            try:
                # Sportsbet uses SessionManager for Token Farm cascade login
                if platform == "sportsbet":
                    from session_manager import SessionManager
                    import token_farm_client
                    # Quick check: is Token Farm reachable?
                    farm_health = await token_farm_client.health()
                    if farm_health.get("status") in ("unreachable", "error"):
                        entry["status"] = "error"
                        entry["error"] = "Token Farm offline — can't login to Sportsbet"
                        results.append(entry)
                        yield f"data: {_json.dumps({'type': 'account', 'index': idx, 'total': total, 'account': entry})}\n\n"
                        continue
                    _sm = SessionManager()
                    session = await _sm.get_or_create(acct)
                else:
                    session = await client.login(
                        email=acct["email"], password=acct["password"],
                        proxy_url=proxy_url, brand_config=brand_config,
                    )

                if not session.get("success"):
                    entry["status"] = "login_failed"
                    entry["error"] = session.get("error", "Unknown")
                    results.append(entry)
                    yield f"data: {_json.dumps({'type': 'account', 'index': idx, 'total': total, 'account': entry})}\n\n"
                    continue

                if not session.get("proxy_url"):
                    session["proxy_url"] = proxy_url

                balances = await client.get_balances(session)
                entry["cash"] = balances.get("cash", 0.0)
                entry["bonus"] = balances.get("bonus", 0.0)
                entry["status"] = "ok"

                # Fetch per-user promo tokens
                if hasattr(client, "get_user_promos"):
                    session["brand_config"] = brand_config
                    user_promos = await client.get_user_promos(session)
                    entry["boost_tokens"] = user_promos.get("boost_tokens", 0)
                    entry["bonus_back_tokens"] = user_promos.get("bonus_back_tokens", 0)
                    entry["deposit_match_tokens"] = user_promos.get("deposit_match_tokens", 0)
                    entry["user_promos"] = user_promos.get("promos", [])
                    entry["redeemed"] = user_promos.get("redeemed", [])
            except Exception as e:
                entry["status"] = "error"
                entry["error"] = str(e)

            results.append(entry)
            yield f"data: {_json.dumps({'type': 'account', 'index': idx, 'total': total, 'account': entry})}\n\n"

        # Fetch public promotions per brand (BetMakers only for now)
        promotions = {}
        for platform, brand in seen_brands:
            if platform == "betmakers":
                promos = await BetMakersClient.get_promotions(brand)
                if promos:
                    promotions[brand] = promos

        # Send final result
        yield f"data: {_json.dumps({'type': 'done', 'accounts': results, 'promotions': promotions})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


# ─── CSV Upload & Validation ──────────────────────────────────────────────

@multi_router.post("/csv/upload", response_model=CsvUploadResponse)
async def api_csv_upload(
    body: CsvUploadRequest,
    user: dict = Depends(_verify_app_token),
):
    """Upload CSV text, validate rows, return preview with validation results."""
    csv_text = body.csv_text
    if not csv_text.strip():
        raise HTTPException(400, "Empty CSV text")

    # Parse CSV
    rows = parse_racing_csv(csv_text)
    if not rows:
        raise HTTPException(400, "No valid rows found in CSV")

    # Validate against registered accounts and supported bookmakers
    validation = await validate_csv_rows(rows, user["username"])

    supported = sum(1 for v in validation if v["valid"])
    unsupported = sum(1 for v in validation if not v["valid"])

    # Collect missing accounts (unique)
    missing = set()
    for v in validation:
        if v.get("error") and "No account" in v["error"]:
            missing.add(f"{v['initials']}@{v.get('brand', v['bookmaker'])}")

    # Create batch
    batch_id = str(uuid.uuid4())
    await create_csv_batch(batch_id, user["username"], "paste-upload", len(rows))

    # Insert rows into DB
    db_rows = []
    for i, (row, val) in enumerate(zip(rows, validation)):
        row_id = str(uuid.uuid4())
        status = "pending" if val["valid"] else "skipped"
        db_row = {
            "id": row_id,
            "batch_id": batch_id,
            "row_index": i,
            "date": row.date,
            "track": row.track,
            "race": str(row.race),
            "horse": row.horse,
            "units": row.units,
            "target_odds": str(row.odds),
            "owner": row.owner,
            "initials": row.initials,
            "bookmaker": row.bookmaker,
            "stake_type": row.stake_type,
            "stake": row.stake,
            "promotion": row.promotion,
            "target_stake": row.target_stake,
            "platform": val.get("platform"),
            "brand": val.get("brand"),
            "account_id": val.get("account_id"),
            "status": status,
        }
        # Sports fields stored in existing columns (track=event, horse=bet_desc)
        if row.row_type == "sports":
            db_row["track"] = row.event
            db_row["horse"] = row.bet_desc
            db_row["race"] = "0"
        db_rows.append(db_row)
    await insert_csv_rows(db_rows)

    # Build preview (all rows, not just 20 — allocations are typically <100)
    preview = []
    for i, (row, val) in enumerate(zip(rows, validation)):
        entry = {
            "row_index": i,
            "row_type": row.row_type,
            "track": row.track,
            "race": row.race,
            "horse": row.horse,
            "event": row.event,
            "bet_desc": row.bet_desc,
            "sport": row.sport,
            "odds": row.odds,
            "min_odds": row.min_odds,
            "initials": row.initials,
            "bookmaker": row.bookmaker,
            "stake": row.stake,
            "stake_type": row.stake_type,
            "valid": val["valid"],
            "platform": val.get("platform"),
            "brand": val.get("brand"),
            "error": val.get("error"),
        }
        preview.append(entry)

    return CsvUploadResponse(
        batch_id=batch_id,
        total_rows=len(rows),
        supported=supported,
        unsupported=unsupported,
        missing_accounts=sorted(missing),
        preview=preview,
    )


# ─── Batch Execution ──────────────────────────────────────────────────────

@multi_router.post("/csv/{batch_id}/execute")
async def api_csv_execute(batch_id: str, user: dict = Depends(_verify_app_token)):
    """Start batch execution as a background task. Returns immediately."""
    batch = await get_batch_status(batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    if batch.get("status") == "running":
        raise HTTPException(409, "Batch is already running")

    # Collect only actionable rows
    actionable_rows = [
        r for r in batch.get("rows", [])
        if r.get("status") == "pending" and r.get("account_id")
    ]
    if not actionable_rows:
        raise HTTPException(400, "No actionable rows in this batch")

    # Mark batch as running
    from multi_database import update_batch_summary
    await update_batch_summary(batch_id, {"status": "running"})

    # Launch background task
    asyncio.create_task(
        _safe_process_batch(batch_id, actionable_rows, user["username"])
    )

    return {
        "ok": True,
        "batch_id": batch_id,
        "actionable_rows": len(actionable_rows),
        "message": "Batch execution started. Poll status endpoint for progress.",
    }


async def _safe_process_batch(batch_id: str, rows: list[dict], username: str):
    """Wrapper to catch and log exceptions from background batch processing."""
    try:
        await process_batch(batch_id, rows, username)
    except Exception as e:
        logger.error(f"Batch {batch_id} crashed: {e}", exc_info=True)
        try:
            from multi_database import update_batch_summary
            await update_batch_summary(batch_id, {
                "status": "error",
                "error": str(e),
            })
        except Exception:
            pass


# ─── Batch Status ──────────────────────────────────────────────────────────

@multi_router.get("/csv/{batch_id}/status")
async def api_csv_status(batch_id: str, user: dict = Depends(_verify_app_token)):
    """Poll batch status and row results."""
    batch = await get_batch_status(batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    return batch


@multi_router.get("/csv/batches")
async def api_csv_batches(
    limit: int = 20, user: dict = Depends(_verify_app_token)
):
    """List recent CSV batches for the current user."""
    batches = await get_recent_batches(user["username"], limit=limit)
    return {"batches": batches}


# ─── Unified Bet Ledger ──────────────────────────────────────────────────

@multi_router.get("/bets")
async def api_list_bets(
    method: str = None,
    brand: str = None,
    status: str = None,
    limit: int = 100,
    user: dict = Depends(_verify_app_token),
):
    """List bets from the unified ledger with optional filters."""
    bets = await get_multi_bets(
        user["username"],
        method=method or None,
        brand=brand or None,
        status=status or None,
        limit=limit,
    )
    return {"bets": bets}


@multi_router.get("/bets/stats")
async def api_bet_stats(
    method: str = None,
    user: dict = Depends(_verify_app_token),
):
    """Get aggregated bet stats (P/L, totals) from the unified ledger."""
    stats = await get_multi_bet_stats(user["username"], method=method or None)
    return stats

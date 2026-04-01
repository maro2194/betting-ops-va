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
    create_csv_batch, insert_csv_rows, get_batch_status,
    get_recent_batches,
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

    # Generate proxy with unique session
    proxy_base = account.get("proxy_base", "")
    if proxy_base:
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
        balance = await client.get_balance(session)
        return {"ok": True, "balance": balance, "message": f"Login OK. Balance: ${balance:.2f}"}
    except Exception as e:
        logger.error(f"Test login failed for {account_id}: {e}")
        raise HTTPException(400, f"Login failed: {str(e)}")


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
        db_rows.append({
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
        })
    await insert_csv_rows(db_rows)

    # Build preview (first 20 rows)
    preview = []
    for i, (row, val) in enumerate(zip(rows[:20], validation[:20])):
        preview.append({
            "row_index": i,
            "track": row.track,
            "race": row.race,
            "horse": row.horse,
            "initials": row.initials,
            "bookmaker": row.bookmaker,
            "stake": row.stake,
            "stake_type": row.stake_type,
            "valid": val["valid"],
            "platform": val.get("platform"),
            "brand": val.get("brand"),
            "error": val.get("error"),
        })

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

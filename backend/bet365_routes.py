"""
bet365 API routes — browser service control, pick feed, placement status.
Mounted into the main FastAPI app.
"""
import asyncio
import logging
import json
import os
import time

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

from bet365_service import bet365_service
from telegram_monitor import telegram_monitor
from pick_pipeline import pick_pipeline
import database

logger = logging.getLogger("bet365_routes")

router = APIRouter(prefix="/api/bet365", tags=["bet365"])


# ─── DB helpers ──────────────────────────────────────────────────────────────

async def init_bet365_tables():
    """Create bet365-specific tables."""
    async with database.pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bet365_picks (
                id TEXT PRIMARY KEY,
                message_id BIGINT,
                timestamp DOUBLE PRECISION NOT NULL,
                source TEXT NOT NULL,
                raw TEXT,
                parsed BOOLEAN DEFAULT FALSE,
                bookie TEXT,
                sport TEXT,
                player TEXT,
                stat TEXT,
                line DOUBLE PRECISION,
                side TEXT,
                odds DOUBLE PRECISION,
                units DOUBLE PRECISION,
                skipped BOOLEAN DEFAULT FALSE,
                skip_reason TEXT,
                attempted BOOLEAN DEFAULT FALSE,
                placed BOOLEAN DEFAULT FALSE,
                actual_odds TEXT,
                stake DOUBLE PRECISION,
                potential_return TEXT,
                error TEXT,
                screenshot TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_bet365_picks_ts ON bet365_picks(timestamp DESC)
        """)
    logger.info("bet365 tables initialized")


async def save_pick_to_db(pick: dict):
    """Save a pick entry to the database."""
    async with database.pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO bet365_picks (
                id, message_id, timestamp, source, raw, parsed, bookie, sport,
                player, stat, line, side, odds, units,
                skipped, skip_reason, attempted, placed,
                actual_odds, stake, potential_return, error, screenshot
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8,
                $9, $10, $11, $12, $13, $14,
                $15, $16, $17, $18,
                $19, $20, $21, $22, $23
            )
        """,
            pick.get("id", ""),
            pick.get("message_id"),
            pick.get("timestamp", time.time()),
            pick.get("source", ""),
            pick.get("raw", ""),
            pick.get("parsed", False),
            pick.get("bookie", ""),
            pick.get("sport", ""),
            pick.get("player", ""),
            pick.get("stat", ""),
            pick.get("line", 0),
            pick.get("side", ""),
            pick.get("odds", 0),
            pick.get("units", 0),
            pick.get("skipped", False),
            pick.get("skip_reason"),
            pick.get("attempted", False),
            pick.get("placed", False),
            pick.get("actual_odds"),
            pick.get("stake", 0),
            pick.get("potential_return"),
            pick.get("error"),
            pick.get("screenshot"),
        )


async def get_picks_from_db(limit: int = 50, placed_only: bool = False) -> list[dict]:
    """Get recent picks from DB."""
    query = "SELECT * FROM bet365_picks"
    if placed_only:
        query += " WHERE attempted = TRUE"
    query += " ORDER BY timestamp DESC LIMIT $1"
    async with database.pool.acquire() as conn:
        rows = await conn.fetch(query, limit)
        return [dict(r) for r in rows]


# ─── Pydantic models ────────────────────────────────────────────────────────

class ManualPickRequest(BaseModel):
    sport: str
    player: str
    stat: str
    line: float
    side: str
    odds: float = 0
    units: float = 1


# ─── Routes ──────────────────────────────────────────────────────────────────

@router.get("/status")
async def bet365_status():
    """Get status of all bet365 subsystems."""
    return {
        "browser": bet365_service.status,
        "telegram": telegram_monitor.status,
        "pipeline": pick_pipeline.status,
    }


@router.post("/browser/start")
async def browser_start():
    """Start the bet365 browser (login)."""
    success = await bet365_service.start()
    if success:
        return {"ok": True, "balance": bet365_service.status.get("balance")}
    raise HTTPException(500, "Failed to start bet365 browser")


@router.post("/browser/stop")
async def browser_stop():
    """Stop the bet365 browser."""
    await bet365_service.stop()
    return {"ok": True}


@router.get("/browser/balance")
async def browser_balance():
    """Get bet365 account balance."""
    balance = await bet365_service.get_balance()
    return {"balance": balance}


@router.post("/telegram/start")
async def telegram_start():
    """Start the Telegram channel monitor."""
    if telegram_monitor.is_running:
        return {"ok": True, "message": "Already running"}

    # Wire up the pipeline callback
    telegram_monitor.set_callback(pick_pipeline.handle_telegram_message)

    # Start in background
    asyncio.create_task(telegram_monitor.run_forever())
    # Give it a moment to connect
    await asyncio.sleep(2)

    return {"ok": True, "running": telegram_monitor.is_running}


@router.post("/telegram/stop")
async def telegram_stop():
    """Stop the Telegram monitor."""
    await telegram_monitor.stop()
    return {"ok": True}


@router.get("/telegram/messages")
async def telegram_messages():
    """Get recent raw messages from Telegram."""
    return {"messages": telegram_monitor.recent_messages}


@router.post("/pipeline/enable")
async def pipeline_enable():
    """Enable auto-placement."""
    pick_pipeline.enable()
    return {"ok": True, "enabled": True}


@router.post("/pipeline/disable")
async def pipeline_disable():
    """Disable auto-placement (picks still parsed, just not placed)."""
    pick_pipeline.disable()
    return {"ok": True, "enabled": False}


@router.get("/picks")
async def get_picks(limit: int = 50, placed_only: bool = False):
    """Get recent picks (from DB if available, else from memory)."""
    try:
        picks = await get_picks_from_db(limit, placed_only)
        if picks:
            return {"picks": picks, "source": "db"}
    except Exception:
        pass
    # Fallback to in-memory log
    log = pick_pipeline.pick_log
    if placed_only:
        log = [p for p in log if p.get("attempted")]
    return {"picks": log[-limit:], "source": "memory"}


@router.post("/picks/manual")
async def manual_pick(req: ManualPickRequest):
    """Manually submit a pick for placement."""
    pick = {
        "sport": req.sport.upper(),
        "player": req.player,
        "stat": req.stat.lower(),
        "line": req.line,
        "side": req.side,
        "odds": req.odds,
        "units": req.units,
        "bookie": "Bet365",
        "source": "manual",
        "raw": f"[manual] {req.player} {req.side} {req.line} {req.stat}",
    }

    if not bet365_service.is_alive:
        raise HTTPException(503, "bet365 browser not running. Start it first.")

    result = await bet365_service.place_pick(pick)
    return result


class MegaBoostRequest(BaseModel):
    sport: str = "AFL"
    match_team: str  # e.g. "Adelaide" or "Carlton"
    stake: int = 20
    boost_index: int = 0  # 0 = first/Mega Boost
    username: str = ""  # optional, defaults to env var
    password: str = ""  # optional, defaults to env var


@router.post("/megaboost")
async def place_megaboost(req: MegaBoostRequest):
    """Place a Mega Boost / Bet Boost via Token Farm browser."""
    from token_farm_client import bet365_login, bet365_megaboost as farm_megaboost

    # Login first if no session
    proxy_url = f"http://{os.environ.get('BET365_PROXY_USER', 'user001')}:{os.environ.get('BET365_PROXY_PASS', 'pizza33')}@{os.environ.get('BET365_PROXY_HOST', '')}:{os.environ.get('BET365_PROXY_PORT', '12323')}"
    username = req.username or os.environ.get("BET365_USERNAME", "")
    password = req.password or os.environ.get("BET365_PASSWORD", "")

    if not username:
        raise HTTPException(400, "No bet365 username provided")

    # Login via Token Farm
    login_result = await bet365_login(username, password, proxy_url=proxy_url)
    if not login_result.get("success"):
        raise HTTPException(502, f"bet365 login failed: {login_result.get('error')}")

    session_id = login_result["session_id"]
    logger.info(f"bet365 megaboost: logged in as {username}, session={session_id}, balance=${login_result.get('balance')}")

    # Place megaboost
    result = await farm_megaboost(
        session_id=session_id,
        sport=req.sport,
        match_team=req.match_team,
        stake=req.stake,
        boost_index=req.boost_index,
    )
    result["username"] = username
    result["balance"] = login_result.get("balance")
    return result


@router.post("/start-all")
async def start_all():
    """Start browser + telegram + enable pipeline — one button."""
    results = {}

    # Start browser
    browser_ok = await bet365_service.start()
    results["browser"] = "ok" if browser_ok else "failed"

    # Wire and start Telegram
    telegram_monitor.set_callback(pick_pipeline.handle_telegram_message)
    pick_pipeline._save_pick_fn = save_pick_to_db
    asyncio.create_task(telegram_monitor.run_forever())
    await asyncio.sleep(2)
    results["telegram"] = "ok" if telegram_monitor.is_running else "failed"

    # Enable pipeline
    pick_pipeline.enable()
    results["pipeline"] = "enabled"

    return results


@router.post("/stop-all")
async def stop_all():
    """Stop everything."""
    pick_pipeline.disable()
    await telegram_monitor.stop()
    await bet365_service.stop()
    return {"ok": True}

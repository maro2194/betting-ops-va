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

# bet365 uses abbreviated team names — map common inputs to what appears on the page
_TEAM_ALIASES = {
    # NBA
    "san antonio": "SA Spurs", "spurs": "SA Spurs", "sa spurs": "SA Spurs",
    "portland": "POR Trail Blazers", "trail blazers": "POR Trail Blazers", "blazers": "POR Trail Blazers",
    "boston": "BOS Celtics", "celtics": "BOS Celtics",
    "philadelphia": "PHI 76ers", "76ers": "PHI 76ers", "sixers": "PHI 76ers",
    "new york": "NY Knicks", "knicks": "NY Knicks", "ny knicks": "NY Knicks",
    "atlanta": "ATL Hawks", "hawks": "ATL Hawks",
    "los angeles lakers": "LA Lakers", "lakers": "LA Lakers",
    "los angeles clippers": "LA Clippers", "clippers": "LA Clippers",
    "golden state": "GS Warriors", "warriors": "GS Warriors",
    "dallas": "DAL Mavericks", "mavericks": "DAL Mavericks", "mavs": "DAL Mavericks",
    "denver": "DEN Nuggets", "nuggets": "DEN Nuggets",
    "milwaukee": "MIL Bucks", "bucks": "MIL Bucks",
    "miami": "MIA Heat", "heat": "MIA Heat",
    "phoenix": "PHX Suns", "suns": "PHX Suns",
    "minnesota": "MIN Timberwolves", "timberwolves": "MIN Timberwolves", "wolves": "MIN Timberwolves",
    "oklahoma city": "OKC Thunder", "thunder": "OKC Thunder", "okc": "OKC Thunder",
    "cleveland": "CLE Cavaliers", "cavaliers": "CLE Cavaliers", "cavs": "CLE Cavaliers",
    "memphis": "MEM Grizzlies", "grizzlies": "MEM Grizzlies",
    "sacramento": "SAC Kings", "kings": "SAC Kings",
    "indiana": "IND Pacers", "pacers": "IND Pacers",
    "orlando": "ORL Magic", "magic": "ORL Magic",
    "houston": "HOU Rockets", "rockets": "HOU Rockets",
    "chicago": "CHI Bulls", "bulls": "CHI Bulls",
    "detroit": "DET Pistons", "pistons": "DET Pistons",
    "toronto": "TOR Raptors", "raptors": "TOR Raptors",
    "charlotte": "CHA Hornets", "hornets": "CHA Hornets",
    "washington": "WAS Wizards", "wizards": "WAS Wizards",
    "brooklyn": "BKN Nets", "nets": "BKN Nets",
    "new orleans": "NO Pelicans", "pelicans": "NO Pelicans",
    "utah": "UTA Jazz", "jazz": "UTA Jazz",
    # NRL — bet365 displays mascot names in match listings and boost cards;
    # alias to identity so the boost-scan match-team filter doesn't drop them.
    "broncos": "Broncos", "cowboys": "Cowboys", "roosters": "Roosters",
    "rabbitohs": "Rabbitohs", "panthers": "Panthers", "storm": "Storm",
    "eels": "Eels", "sea eagles": "Sea Eagles", "knights": "Knights",
    "raiders": "Raiders", "sharks": "Sharks", "titans": "Titans",
    "warriors": "Warriors", "bulldogs": "Bulldogs", "dragons": "Dragons",
    "wests tigers": "Wests Tigers", "dolphins": "Dolphins",
    # AFL
    "magpies": "Collingwood", "blues": "Carlton", "cats": "Geelong",
    "bombers": "Essendon", "demons": "Melbourne", "swans": "Sydney",
    "power": "Port Adelaide", "crows": "Adelaide", "dockers": "Fremantle",
    "eagles": "West Coast", "giants": "GWS", "lions": "Brisbane Lions",
    "kangaroos": "North Melbourne", "saints": "St Kilda", "tigers": "Richmond",
    "suns": "Gold Coast", "hawks": "Hawthorn",
}


def _resolve_team(team: str) -> str:
    """Resolve a team alias to the bet365 display name."""
    return _TEAM_ALIASES.get(team.lower().strip(), team)

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
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bg_jobs (
                job_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                result JSONB,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_bg_jobs_created ON bg_jobs(created_at)
        """)
    logger.info("bet365 tables initialized")


async def _save_job(job_id: str, kind: str, status: str, result: dict | None = None):
    async with database.pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO bg_jobs (job_id, kind, status, result)
               VALUES ($1, $2, $3, $4::jsonb)
               ON CONFLICT (job_id) DO UPDATE SET status = $3, result = $4::jsonb""",
            job_id, kind, status, json.dumps(result) if result is not None else None,
        )


async def _get_job(job_id: str) -> dict | None:
    async with database.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT status, result FROM bg_jobs WHERE job_id = $1", job_id)
        if not row:
            return None
        result = row["result"]
        if isinstance(result, str):
            result = json.loads(result)
        return {"status": row["status"], "result": result}


async def save_pick_to_db(pick: dict):
    """Save a pick entry to the database.

    If the pick was actually placed (pick['placed']==True), fires an emit to the
    external BetOps tracker. Skipped/attempted-but-failed picks are NOT emitted —
    only real placements end up in BetOps. Fire-and-forget per the standard
    betops_sync contract.
    """
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
            float(pick.get("line", 0) or 0),
            pick.get("side", ""),
            float(pick.get("odds", 0) or 0),
            float(pick.get("units", 0) or 0),
            pick.get("skipped", False),
            pick.get("skip_reason"),
            pick.get("attempted", False),
            pick.get("placed", False),
            pick.get("actual_odds"),
            float(pick.get("stake", 0) or 0),
            pick.get("potential_return"),
            pick.get("error"),
            pick.get("screenshot"),
        )

    if pick.get("placed"):
        try:
            import asyncio as _asyncio
            from betops_sync import emit_bet365_pick_to_betops
            # The BotOps user gates BETOPS_USERNAME_ALLOWLIST and seeds the
            # bookie_accounts email lookup. pick["username"] is the *bet365 login
            # handle* (which doubles as bookie_accounts.email for all rows) —
            # emit_bet365_pick_to_betops uses it directly for the account email.
            _asyncio.create_task(emit_bet365_pick_to_betops(dict(pick), "maro", database.pool))
        except Exception as _e:
            logger.error("BetOps bet365 emit scheduling failed: %s", _e)


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
    from token_farm_client import bet365_get_or_login, bet365_megaboost as farm_megaboost

    # Login first if no session
    proxy_url = f"http://{os.environ.get('BET365_PROXY_USER', 'user001')}:{os.environ.get('BET365_PROXY_PASS', 'pizza33')}@{os.environ.get('BET365_PROXY_HOST', '')}:{os.environ.get('BET365_PROXY_PORT', '12323')}"
    username = req.username or os.environ.get("BET365_USERNAME", "")
    password = req.password or os.environ.get("BET365_PASSWORD", "")

    if not username:
        raise HTTPException(400, "No bet365 username provided")

    # Reuse existing session or login via Token Farm
    login_result = await bet365_get_or_login(username, password, proxy_url=proxy_url)
    if not login_result.get("success"):
        raise HTTPException(502, f"bet365 login failed: {login_result.get('error')}")

    session_id = login_result["session_id"]
    resolved_team = _resolve_team(req.match_team)
    logger.info(f"bet365 megaboost: logged in as {username}, session={session_id}, balance=${login_result.get('balance')}")

    # Place megaboost
    result = await farm_megaboost(
        session_id=session_id,
        sport=req.sport,
        match_team=resolved_team,
        stake=req.stake,
        boost_index=req.boost_index,
    )
    result["username"] = username
    result["balance"] = login_result.get("balance")

    import uuid, time as _time
    asyncio.create_task(save_pick_to_db({
        "id": str(uuid.uuid4()),
        "timestamp": _time.time(),
        "source": "bet365_megaboost",
        "bookie": "Bet365",
        "sport": req.sport,
        "player": req.match_team,
        "stat": "megaboost",
        "raw": f"[megaboost] {req.sport} {req.match_team}",
        "parsed": True,
        "attempted": True,
        "placed": result.get("success", False),
        "stake": req.stake,
        "odds": result.get("boosted_odds") or result.get("odds", 0),
        "actual_odds": str(result.get("boosted_odds") or result.get("odds", "")),
        "username": username,
        "line": 0,
        "side": "",
        "units": 1,
        "error": result.get("error"),
    }))
    return result


class ScanBoostsRequest(BaseModel):
    sport: str = "HOME"  # HOME = homepage, or NRL/AFL/NBA + match_team
    match_team: str = ""


@router.post("/scan-boosts")
async def scan_boosts(req: ScanBoostsRequest):
    """Start boost scan as async job. Returns job_id to poll."""
    import uuid as _uuid
    from token_farm_client import bet365_get_or_login, bet365_megaboost, bet365_scan_boosts

    username = os.environ.get("BET365_USERNAME", "")
    password = os.environ.get("BET365_PASSWORD", "")
    if not username:
        raise HTTPException(400, "No bet365 username configured")

    job_id = str(_uuid.uuid4())[:8]
    await _save_job(job_id, "scan_boosts", "running")

    async def run_scan():
        try:
            login_result = await bet365_get_or_login(username, password)
            if not login_result.get("success"):
                await _save_job(job_id, "scan_boosts", "done", {"success": False, "error": f"Login failed: {login_result.get('error')}", "boosts": []})
                return

            session_id = login_result["session_id"]
            result = await bet365_scan_boosts(session_id, sport=req.sport, match_team=_resolve_team(req.match_team) if req.match_team else "")

            result["balance"] = login_result.get("balance")
            result["session_id"] = session_id
            await _save_job(job_id, "scan_boosts", "done", result)
        except Exception as e:
            await _save_job(job_id, "scan_boosts", "done", {"success": False, "error": str(e), "boosts": []})

    asyncio.create_task(run_scan())
    return {"job_id": job_id, "status": "running"}


@router.get("/scan-boosts/status/{job_id}")
async def scan_boosts_status(job_id: str):
    """Poll for scan-boosts job result."""
    job = await _get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


class MegaBoostAllRequest(BaseModel):
    sport: str = "NRL"
    match_team: str
    stake: int = 20
    boost_index: int = 0
    accounts: list[str] | None = None  # Optional filter: list of usernames (None = all)
    stakes: dict[str, int] | None = None  # Per-account stakes: {"username": stake}


@router.post("/megaboost-all")
async def place_megaboost_all(req: MegaBoostAllRequest):
    """Start megaboost placement as async job. Returns job_id to poll for results."""
    import uuid as _uuid
    from token_farm_client import bet365_megaboost_all

    job_id = str(_uuid.uuid4())[:8]
    await _save_job(job_id, "megaboost_all", "running")

    async def run_job():
        import uuid, time as _time
        try:
            resolved_team = _resolve_team(req.match_team)
            all_results = []
            if req.stakes:
                groups = {}
                for acc, s in req.stakes.items():
                    groups.setdefault(s, []).append(acc)
                for grp_stake, grp_accounts in groups.items():
                    r = await bet365_megaboost_all(
                        sport=req.sport, match_team=resolved_team,
                        stake=grp_stake, boost_index=req.boost_index,
                        accounts=grp_accounts,
                    )
                    all_results.extend(r.get("results", []))
                result = {"results": all_results, "success": True}
            else:
                result = await bet365_megaboost_all(
                    sport=req.sport, match_team=resolved_team,
                    stake=req.stake, boost_index=req.boost_index,
                    accounts=req.accounts,
                )
            log = []
            for r in result.get("results", []):
                uname = r.get("username", "?")
                acc_stake = (req.stakes or {}).get(uname, req.stake)
                if r.get("success"):
                    odds_str = r.get("boosted_odds") or r.get("odds", "?")
                    log.append(f"{uname}: Placed ${acc_stake} @ {odds_str}")
                else:
                    log.append(f"{uname}: FAILED — {r.get('error', 'unknown')}")
                await save_pick_to_db({
                    "id": str(uuid.uuid4()),
                    "timestamp": _time.time(),
                    "source": "bet365_megaboost",
                    "bookie": "Bet365",
                    "sport": req.sport,
                    "player": req.match_team,
                    "stat": "megaboost",
                    "raw": f"[megaboost] {req.sport} {req.match_team}",
                    "parsed": True,
                    "attempted": True,
                    "placed": r.get("success", False),
                    "stake": acc_stake,
                    "odds": float(r.get("boosted_odds") or r.get("odds", 0) or 0),
                    "actual_odds": str(r.get("boosted_odds") or r.get("odds", "")),
                    "username": r.get("username", ""),
                    "line": 0,
                    "side": "",
                    "units": 1,
                    "error": r.get("error"),
                })
            placed = sum(1 for r in result.get("results", []) if r.get("success"))
            total = len(result.get("results", []))
            log.append(f"Done: {placed}/{total} placed successfully")
            result["log"] = log
            result["succeeded"] = placed
            result["total"] = total
            await _save_job(job_id, "megaboost_all", "done", result)
        except Exception as e:
            await _save_job(job_id, "megaboost_all", "error", {"error": str(e)})

    asyncio.create_task(run_job())
    return {"job_id": job_id, "status": "running"}


@router.get("/megaboost-all/status/{job_id}")
async def megaboost_all_status(job_id: str):
    """Poll for megaboost-all job result."""
    job = await _get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


class ManualTrackRequest(BaseModel):
    username: str
    sport: str
    match_team: str
    stake: float
    odds: float
    placed_at: Optional[str] = None  # ISO string, defaults to now


@router.post("/megaboost/track")
async def manual_track_megaboost(bets: list[ManualTrackRequest]):
    """Manually backfill megaboost placements into bet365_picks + BetOps.

    Accepts a list so you can submit multiple bets (e.g. one per account) in one call.
    """
    import uuid, time as _time
    saved = []
    for req in bets:
        import dateutil.parser as _dp
        ts = _dp.parse(req.placed_at).timestamp() if req.placed_at else _time.time()
        pick = {
            "id": str(uuid.uuid4()),
            "timestamp": ts,
            "source": "bet365_megaboost",
            "bookie": "Bet365",
            "sport": req.sport,
            "player": req.match_team,
            "stat": "megaboost",
            "raw": f"[megaboost-backfill] {req.sport} {req.match_team}",
            "parsed": True,
            "attempted": True,
            "placed": True,
            "stake": req.stake,
            "odds": req.odds,
            "actual_odds": str(req.odds),
            "username": req.username,
            "line": 0,
            "side": "",
            "units": 1,
        }
        await save_pick_to_db(pick)
        saved.append({"id": pick["id"], "username": req.username})
    return {"tracked": len(saved), "bets": saved}


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

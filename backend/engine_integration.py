"""
Integration layer between the Execution Engine and existing BotOps TAB functions.

Implements the callbacks required by execution_engine.py using existing:
  - resolver.py: resolve_csb_bet() for live odds
  - betting.py: place_sgm_bet(), place_multi_bet() for placement
  - main.py: _kelly_drift_stake() for Kelly calculations
  - database.py: get_bets() for confirmed history
"""
import asyncio
import logging
import random
import time

from execution_engine import (
    Bet, Account, ChunkResult, RunState,
    normalize_runner_id, run_execution, retry_failed_bet,
)
from resolver import resolve_csb_bet
from betting import place_sgm_bet, place_multi_bet

logger = logging.getLogger("engine_integration")


# ─── Callback: fetch_live_odds ────────────────────────────────────────────────

async def fetch_live_odds(bet: Bet, account: Account) -> float | None:
    """
    Fetch live TAB odds for a bet by resolving against current markets.
    Returns combined odds as float, or None if unavailable.
    """
    session = account.session
    token = session.get("token", "")
    proxy_url = session.get("proxy_url", "")
    sport = bet.sport or "NBA"
    competition = bet.competition or "NBA"

    try:
        resolved = await asyncio.to_thread(
            resolve_csb_bet,
            token=token,
            proxy_url=proxy_url,
            bet_type=bet.bet_type,
            game_id=bet.game_id,
            bet_description=bet.bet_description,
            stake="10",  # Doesn't matter for resolution
            min_odds=0,  # Don't filter — we check min_odds in the engine
            sport=sport,
            competition=competition,
        )

        if resolved.get("resolved") and resolved.get("combined_odds"):
            odds = float(resolved["combined_odds"])
            # Stash resolved propositions per-account (avoid shared state race)
            bet.raw[f"_resolved_{account.id}"] = resolved
            return odds

        logger.warning("Live odds unavailable for %s: %s", bet.runner_id, resolved.get("error"))
        return None

    except Exception as e:
        logger.error("fetch_live_odds error for %s: %s", bet.runner_id, e)
        return None


# ─── Callback: get_kelly_stake ────────────────────────────────────────────────

def get_kelly_stake(bet: Bet, odds: float, unit_size: float = 75) -> float:
    """
    Calculate Kelly-adjusted stake at given odds.

    Uses Shadow's Kelly ratio formula:
    - True probability from min_odds
    - EV at given odds
    - Kelly fraction → units × unit_size

    Returns stake in dollars (not yet rounded to $10).
    """
    if not odds or odds <= 1 or not bet.min_odds or bet.min_odds <= 1:
        return bet.units * unit_size

    tipped_odds = bet.odds
    min_odds = bet.min_odds
    units = bet.units

    if odds >= tipped_odds:
        # Odds same or better — full stake
        return units * unit_size

    if odds <= min_odds:
        return 0  # Below min — no edge

    # True probability from min odds
    p = 1.0 / min_odds

    # EV at tipped and actual
    ev_target = (p * tipped_odds) - 1
    ev_actual = (p * odds) - 1

    if ev_target <= 0 or ev_actual <= 0:
        return 0

    # Kelly fractions
    kelly_target = ev_target / (tipped_odds - 1)
    kelly_actual = ev_actual / (odds - 1)

    multiplier = kelly_actual / kelly_target
    return max(0, units * multiplier * unit_size)


# ─── Callback: place_bet ──────────────────────────────────────────────────────

async def place_bet(account: Account, bet: Bet, stake: float, live_odds: float) -> ChunkResult:
    """
    Place a single bet chunk on TAB via the existing placement functions.
    Uses the most recently resolved propositions for accurate placement.
    """
    session = account.session
    legacy_token = session.get("legacy_token", "")
    account_number = session.get("account_number", "")
    proxy_url = session.get("proxy_url", "")
    token = session.get("token", "")
    sport = bet.sport or "NBA"
    competition = bet.competition or "NBA"

    if not legacy_token:
        return ChunkResult(success=False, error="No legacy token — re-login required")

    # Use per-account resolved data (avoids shared state race between workers)
    resolved = bet.raw.get(f"_resolved_{account.id}")
    if not resolved or not resolved.get("propositions"):
        try:
            resolved = await asyncio.to_thread(
                resolve_csb_bet,
                token=token, proxy_url=proxy_url,
                bet_type=bet.bet_type, game_id=bet.game_id,
                bet_description=bet.bet_description, stake=str(stake),
                min_odds=0, sport=sport, competition=competition,
            )
        except Exception as e:
            return ChunkResult(success=False, error=f"Resolution failed: {e}")

    if not resolved.get("resolved") or not resolved.get("propositions"):
        return ChunkResult(success=False, error=resolved.get("error", "Resolution failed"))

    combined_odds = str(resolved.get("combined_odds", live_odds))
    propositions = resolved["propositions"]

    # Anti-detection: small random delay before placement
    await asyncio.sleep(random.uniform(0.3, 1.2))

    MAX_PRICE_RETRIES = 3
    place_result = None

    for attempt in range(1, MAX_PRICE_RETRIES + 1):
        try:
            if bet.is_sgm:
                place_result = await asyncio.to_thread(
                    place_sgm_bet,
                    legacy_token=legacy_token,
                    account_number=account_number,
                    propositions=propositions,
                    combined_odds=combined_odds,
                    stake=str(stake),
                    proxy_url=proxy_url,
                )
            else:
                place_result = await asyncio.to_thread(
                    place_multi_bet,
                    legacy_token=legacy_token,
                    account_number=account_number,
                    legs=[{"propositionId": p.get("propositionId"), "odds": p.get("odds")}
                          for p in propositions],
                    stake=str(stake),
                    proxy_url=proxy_url,
                )
        except Exception as e:
            place_result = {"success": False, "error": str(e)}

        # Handle price changed — re-resolve and retry
        err = place_result.get("error", "")
        if not place_result.get("success") and "price" in err.lower() and "changed" in err.lower() and attempt < MAX_PRICE_RETRIES:
            logger.info("Price changed on attempt %d for %s, re-resolving...", attempt, bet.runner_id)
            try:
                resolved = await asyncio.to_thread(
                    resolve_csb_bet,
                    token=token, proxy_url=proxy_url,
                    bet_type=bet.bet_type, game_id=bet.game_id,
                    bet_description=bet.bet_description, stake=str(stake),
                    min_odds=0, sport=sport, competition=competition,
                )
                if resolved.get("resolved"):
                    combined_odds = str(resolved["combined_odds"])
                    propositions = resolved["propositions"]
                    bet.raw[f"_resolved_{account.id}"] = resolved
            except Exception:
                pass
            await asyncio.sleep(random.uniform(0.3, 0.8))
            continue

        break  # Success or non-price error

    if place_result and place_result.get("success"):
        ticket = place_result.get("ticket_number")
        if not ticket:
            logger.error("Placement returned success but no ticket_number — treating as failed. Response: %s", place_result.get("details", {}))
            return ChunkResult(success=False, error="Placement returned success but no ticket/TSN — bet not confirmed")
        # Build V1-compatible leg details for death riding
        detailed_legs = []
        game_id = bet.game_id or ""
        for prop in propositions:
            prop_name = prop.get("name", "")
            prop_odds = prop.get("odds", "")
            detailed_legs.append({"name": f"{prop_name}", "odds": str(prop_odds), "propositionId": prop.get("propositionId")})
        return ChunkResult(
            success=True,
            confirmed_amount=stake,
            actual_odds=float(combined_odds) if combined_odds else live_odds,
            bet_id=ticket,
            tsn=ticket,
            legs=detailed_legs,
        )
    else:
        return ChunkResult(
            success=False,
            error=place_result.get("error", "Placement failed") if place_result else "No result",
        )


# ─── Callback: get_confirmed_history ──────────────────────────────────────────

async def get_confirmed_history(account: Account, runner_id: str) -> float:
    """
    Get total confirmed placed amount for a runner_id from bet history DB.
    Source of truth for retries — NOT in-memory state.
    """
    try:
        from database import get_bets
        bets = await get_bets(
            username=account.session.get("_username", ""),
            account_number=account.id,
        )
        total = 0.0
        for bet in bets:
            # Reconstruct runner_id from stored bet data
            game_id = bet.get("game_id", "")
            legs = bet.get("legs", [])
            desc = "/".join(l.get("name", "") for l in legs if isinstance(l, dict))
            is_sgm = bet.get("bet_type", "").upper() == "SGM"
            stored_runner_id = normalize_runner_id(game_id, desc, is_sgm)

            if stored_runner_id == runner_id and bet.get("status") not in ("failed", "void"):
                stake = float(str(bet.get("stake", 0)).replace("$", "").replace(",", ""))
                total += stake
        return total
    except Exception as e:
        logger.error("get_confirmed_history error for %s: %s", runner_id, e)
        return 0.0


# ─── API-facing functions ─────────────────────────────────────────────────────

def parse_bets_to_engine(parsed_bets: list[dict], sport: str, competition: str) -> list[Bet]:
    """Convert frontend-parsed bet dicts to engine Bet objects."""
    bets = []
    for b in parsed_bets:
        bets.append(Bet(
            bet_type=b.get("bet_type", "SGM"),
            game_id=b.get("game_id", ""),
            bet_description=b.get("bet", ""),
            odds=float(b.get("odds", 0)),
            min_odds=float(b.get("min_odds", 0)),
            ev_pct=float(b.get("ev_pct", 0)),
            units=float(b.get("units", 1)),
            sport=sport,
            competition=competition,
            leg_odds=b.get("leg_odds", []),
            raw=dict(b),
        ))
    return bets


def sessions_to_accounts(sessions: dict, account_list: list[dict], username: str) -> list[Account]:
    """Convert BotOps sessions + DB accounts to engine Account objects."""
    accounts = []
    for acct_info in account_list:
        acct_num = str(acct_info.get("account_number", ""))
        # Find matching session
        for sid, s in sessions.items():
            if s.get("account_number") == acct_num and s.get("legacy_token"):
                accounts.append(Account(
                    id=acct_num,
                    label=acct_info.get("label", acct_num),
                    session={**s, "_username": username},
                    enabled=True,
                ))
                break
    return accounts


async def execute_csb_run(
    parsed_bets: list[dict],
    account_infos: list[dict],
    sessions: dict,
    username: str,
    sport: str = "NBA",
    competition: str = "NBA",
    unit_size: float = 75,
    on_result=None,
) -> dict:
    """
    Run the full execution engine for a CSB batch.

    Args:
        parsed_bets: List of bet dicts from CSV parse
        account_infos: List of account dicts from DB (with label, account_number)
        sessions: In-memory sessions dict
        username: App username for DB queries
        sport: Sport name for TAB API
        competition: Competition for TAB API
        unit_size: Dollar value per unit
        on_result: Optional callback for real-time updates

    Returns: execution results dict
    """
    bets = parse_bets_to_engine(parsed_bets, sport, competition)
    accounts = sessions_to_accounts(sessions, account_infos, username)

    if not bets:
        return {"error": "No bets to execute", "regular": [], "sgm": [], "state": {}}
    if not accounts:
        return {"error": "No accounts with active sessions", "regular": [], "sgm": [], "state": {}}

    # Create Kelly callback with the configured unit_size
    def kelly_fn(bet: Bet, odds: float) -> float:
        return get_kelly_stake(bet, odds, unit_size)

    results = await run_execution(
        bet_list=bets,
        account_list=accounts,
        fetch_live_odds=fetch_live_odds,
        get_kelly_stake=kelly_fn,
        place_bet=place_bet,
        on_result=on_result,
    )

    # Save successful bets to DB
    try:
        from database import save_bet
        all_results = results.get("regular", []) + results.get("sgm", [])
        for entry in all_results:
            r = entry.get("result")
            if r and r.success:
                account_label = entry.get("account_label", entry.get("account", ""))
                # Use detailed legs from placement (V1-compatible format for death riding)
                legs = r.legs if r.legs else [{"name": entry.get("bet_description", "")}]
                await save_bet(username, {
                    "account_number": entry["account"],
                    "account_label": account_label,
                    "tsn": r.tsn or r.bet_id,
                    "bet_type": "SGM" if "::" in entry.get("runner_id", "") else "Multi",
                    "legs": legs,
                    "combined_odds": str(r.actual_odds),
                    "stake": str(r.confirmed_amount),
                    "status": "Pending",
                    "source": "csb_engine",
                    "game_id": entry.get("game_id", ""),
                })
    except Exception as e:
        logger.error("Failed to save engine bets to DB: %s", e)

    return results

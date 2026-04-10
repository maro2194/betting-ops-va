"""
BetOps Execution Engine — Anti-Detection, Dynamic Re-Pricing & Safe Re-Placement.

Implements Shadow's v3 spec:
  - Pseudo-random shuffle per run (unique starting bets per account)
  - Lazy evaluation with live odds fetch per chunk
  - Mid-split dynamic re-pricing ("Cop the Drop")
  - Per-runner_id synchronous locking
  - Safe retry with history-based source of truth
  - SGM two-step game+bet shuffle

See: BetOps_Execution_Engine_Spec_v3.docx
"""
import asyncio
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable

logger = logging.getLogger("execution_engine")


# ─── Canonical Runner ID ──────────────────────────────────────────────────────

def normalize_runner_id(game_id: str, bet_description: str, is_sgm: bool) -> str:
    """
    Canonical runner key used for ALL tracking: placed_totals, allocated_totals,
    history lookups, and retries.

    MUST be called identically at every call site. Defined once here, imported everywhere.

    SGM:   "{GAME_ID}::{normalized_description}"
    Multi: "{normalized_description}"
    """
    desc = (bet_description or "").strip().lower()
    desc = re.sub(r'\s+', ' ', desc)       # collapse whitespace
    desc = desc.replace(" / ", "/")         # normalize slash spacing
    desc = re.sub(r'\s*\+\s*', '+', desc)  # normalize plus spacing: "20 + Points", "20+ Points", "20 +Points" all → "20+Points"
    if is_sgm and game_id:
        return f"{game_id.strip().upper()}::{desc}"
    return desc


# ─── Run State ────────────────────────────────────────────────────────────────

@dataclass
class RunState:
    """Per-run mutable state. Created fresh for each execution run."""
    placed_totals: dict[str, float] = field(default_factory=dict)      # runner_id -> total confirmed placed
    allocated_totals: dict[str, float] = field(default_factory=dict)    # runner_id -> total allocated this round
    account_balances: dict[str, float] = field(default_factory=dict)    # account_id -> balance at run start
    skipped_runners: dict[str, set] = field(default_factory=dict)       # runner_id -> set of account_ids
    locks: dict[str, asyncio.Lock] = field(default_factory=dict)        # runner_id -> asyncio.Lock


def get_lock(state: RunState, runner_id: str) -> asyncio.Lock:
    """Get or create a per-runner_id lock."""
    if runner_id not in state.locks:
        state.locks[runner_id] = asyncio.Lock()
    return state.locks[runner_id]


# ─── Bet & Account Types ─────────────────────────────────────────────────────

@dataclass
class Bet:
    """A single bet from the CSV."""
    bet_type: str           # "SGM" or "Multi"
    game_id: str            # e.g. "20260410_BOS_NYK" (SGMs) or "" (Multis)
    bet_description: str    # e.g. "Karl-Anthony Towns 20+ Points/Sam Hauser 10+ Points"
    odds: float             # tipped odds from CSV
    min_odds: float         # minimum acceptable odds
    ev_pct: float           # expected value %
    units: float            # Kelly units
    sport: str = ""
    competition: str = ""
    leg_odds: list[float] = field(default_factory=list)
    raw: dict = field(default_factory=dict)  # original parsed CSV row

    @property
    def is_sgm(self) -> bool:
        return self.bet_type.upper() == "SGM"

    @property
    def runner_id(self) -> str:
        return normalize_runner_id(self.game_id, self.bet_description, self.is_sgm)


@dataclass
class Account:
    """A TAB account available for betting."""
    id: str                 # account_number or session_id
    label: str              # e.g. "AGK"
    session: dict           # session data (token, legacy_token, proxy_url, etc.)
    enabled: bool = True


@dataclass
class ChunkResult:
    """Result from placing a single chunk."""
    success: bool
    confirmed_amount: float = 0
    actual_odds: float = 0
    error: str = ""
    bet_id: str = ""
    tsn: str = ""


# ─── Callbacks (injected by caller) ──────────────────────────────────────────
# The engine doesn't know HOW to fetch odds or place bets — the caller provides these.

FetchLiveOdds = Callable[[Bet, Account], Awaitable[Optional[float]]]
GetKellyStake = Callable[[Bet, float], float]  # (bet, odds) -> stake
PlaceBet = Callable[[Account, Bet, float, float], Awaitable[ChunkResult]]  # (account, bet, stake, odds) -> result
GetConfirmedHistory = Callable[[Account, str], Awaitable[float]]  # (account, runner_id) -> total placed


# ─── Shuffle Helpers ──────────────────────────────────────────────────────────

def build_queue(items: list, start_idx: int) -> list:
    """Return items starting from start_idx, wrapping around."""
    n = len(items)
    return [items[(start_idx + i) % n] for i in range(n)]


def group_by_game(bets: list[Bet]) -> dict[str, list[Bet]]:
    """Group SGM bets by game_id."""
    groups: dict[str, list[Bet]] = {}
    for bet in bets:
        key = bet.game_id or "unknown"
        groups.setdefault(key, []).append(bet)
    return groups


# ─── Core: process_chunk ──────────────────────────────────────────────────────

async def process_chunk(
    bet: Bet,
    account: Account,
    state: RunState,
    fetch_live_odds: FetchLiveOdds,
    get_kelly_stake: GetKellyStake,
    place_bet: PlaceBet,
) -> Optional[ChunkResult]:
    """
    Process a single bet chunk for a single account.
    Acquires per-runner lock, fetches live odds, calculates stake, places or skips.
    Returns ChunkResult or None if skipped.
    """
    runner_id = bet.runner_id
    lock = get_lock(state, runner_id)

    async with lock:
        # Step 1: fetch live odds
        live_odds = await fetch_live_odds(bet, account)
        if live_odds is None:
            logger.warning("SKIP | runner=%s acct=%s | live odds unavailable", runner_id, account.label)
            return ChunkResult(success=False, error="Live odds unavailable")

        # Step 2: check minimum odds
        if live_odds < bet.min_odds:
            logger.info("SKIP | runner=%s acct=%s | live_odds=%.2f below min=%.2f",
                        runner_id, account.label, live_odds, bet.min_odds)
            state.skipped_runners.setdefault(runner_id, set()).add(account.id)
            return None  # abort — do not redistribute

        # Step 3: check if already fully covered
        already_placed = state.placed_totals.get(runner_id, 0)
        original_target = get_kelly_stake(bet, bet.odds)
        # original_target is fixed at tipped odds — does not increase if odds improve

        if already_placed >= original_target:
            logger.info("SKIP | runner=%s acct=%s | already_placed=%.0f >= target=%.0f",
                        runner_id, account.label, already_placed, original_target)
            return None

        # Step 4: recalculate Kelly at live odds (only downward adjustment)
        kelly_target = min(original_target, get_kelly_stake(bet, live_odds))
        if already_placed >= kelly_target:
            logger.info("ABORT | runner=%s acct=%s | already_placed=%.0f >= kelly=%.0f",
                        runner_id, account.label, already_placed, kelly_target)
            return None

        # Step 5: remaining budget
        remaining_budget = kelly_target - already_placed

        # Step 6: identify eligible remaining accounts
        # (handled at caller level — this function processes one account at a time)
        # For single-account chunk, raw_chunk = remaining_budget / num_eligible
        # Caller should pass the right share. Here we use remaining_budget directly
        # and let the ceiling cap handle overshoot.

        # Step 7-8: chunk calculation (caller handles multi-account splitting)
        raw_chunk = remaining_budget

        # Step 9: apply ceiling cap
        allocated_so_far = state.allocated_totals.get(runner_id, 0)
        ceiling = kelly_target - allocated_so_far
        chunk = min(raw_chunk, ceiling)

        # Step 10: round down to nearest $10
        chunk = (chunk // 10) * 10

        # Step 11: minimum bet check
        if chunk < 10:
            logger.info("SKIP | runner=%s acct=%s | chunk=%.0f below min bet",
                        runner_id, account.label, chunk)
            return None

        # Check account balance
        balance = state.account_balances.get(account.id, 0)
        if chunk > balance:
            chunk = (balance // 10) * 10
            if chunk < 10:
                logger.info("SKIP | runner=%s acct=%s | insufficient balance",
                            runner_id, account.label)
                return None

        # Step 12: place bet
        logger.info("PLACING | runner=%s acct=%s | chunk=$%.0f odds=%.2f",
                     runner_id, account.label, chunk, live_odds)

        result = await place_bet(account, bet, chunk, live_odds)

        # Step 13: confirm and update state
        if result.success:
            confirmed = result.confirmed_amount or chunk
            state.placed_totals[runner_id] = state.placed_totals.get(runner_id, 0) + confirmed
            state.allocated_totals[runner_id] = state.allocated_totals.get(runner_id, 0) + confirmed
            state.account_balances[account.id] = balance - confirmed
            logger.info("CONFIRMED | runner=%s acct=%s | placed=$%.0f running_total=$%.0f",
                         runner_id, account.label, confirmed, state.placed_totals[runner_id])
        else:
            logger.warning("FAILED | runner=%s acct=%s | error=%s",
                           runner_id, account.label, result.error)

        return result


# ─── Pipeline 1: Regular Multis ──────────────────────────────────────────────

async def _run_account_multi_queue(
    account: Account,
    queue: list[Bet],
    state: RunState,
    fetch_live_odds: FetchLiveOdds,
    get_kelly_stake: GetKellyStake,
    place_bet: PlaceBet,
    on_result: Callable | None = None,
    min_delay: float = 2.0,
    max_delay: float = 6.0,
) -> list[dict]:
    """Process one account's bet queue. Per-account delays between bets."""
    results = []
    for i, bet in enumerate(queue):
        if i > 0:
            delay = random.uniform(min_delay, max_delay)
            await asyncio.sleep(delay)

        result = await process_chunk(
            bet, account, state,
            fetch_live_odds, get_kelly_stake, place_bet,
        )
        entry = {
            "account": account.id,
            "account_label": account.label,
            "runner_id": bet.runner_id,
            "bet_description": bet.bet_description,
            "game_id": bet.game_id,
            "result": result,
        }
        results.append(entry)
        if on_result:
            on_result(entry)

    return results


async def run_multis(
    bets: list[Bet],
    accounts: list[Account],
    state: RunState,
    fetch_live_odds: FetchLiveOdds,
    get_kelly_stake: GetKellyStake,
    place_bet: PlaceBet,
    on_result: Callable | None = None,
    min_delay: float = 2.0,
    max_delay: float = 6.0,
) -> list[dict]:
    """Execute regular multi bets with shuffled starting positions per account.
    Accounts run CONCURRENTLY — delay is per-account between its own bets."""
    if not bets or not accounts:
        return []

    n = len(bets)

    # Generate one pseudo-random shuffle of bet indices
    shuffled_indices = list(range(n))
    random.shuffle(shuffled_indices)

    # Assign each account a unique starting position
    account_queues = {}
    for i, account in enumerate(accounts):
        start_pos = shuffled_indices[i % n]
        account_queues[account.id] = build_queue(bets, start_pos)

    # Run all accounts concurrently — per-runner locks ensure safe shared state
    tasks = [
        _run_account_multi_queue(
            account, account_queues[account.id], state,
            fetch_live_odds, get_kelly_stake, place_bet, on_result,
            min_delay, max_delay,
        )
        for account in accounts
    ]
    all_results = await asyncio.gather(*tasks)

    # Flatten
    results = []
    for account_results in all_results:
        results.extend(account_results)

    return results


# ─── Pipeline 2: SGMs ────────────────────────────────────────────────────────

async def run_sgms(
    bets: list[Bet],
    accounts: list[Account],
    state: RunState,
    fetch_live_odds: FetchLiveOdds,
    get_kelly_stake: GetKellyStake,
    place_bet: PlaceBet,
    on_result: Callable | None = None,
    min_delay: float = 2.0,
    max_delay: float = 6.0,
) -> list[dict]:
    """Execute SGM bets with two-step shuffle (games then bets within games).
    Accounts run CONCURRENTLY — delay is per-account between its own bets."""
    if not bets or not accounts:
        return []

    results = []
    games = group_by_game(bets)
    game_ids = list(games.keys())

    if not game_ids:
        return []

    # Step 1: Shuffle game list, assign starting game per account
    shuffled_games = game_ids[:]
    random.shuffle(shuffled_games)

    account_game_queues = {}
    for i, account in enumerate(accounts):
        start_idx = i % len(shuffled_games)
        account_game_queues[account.id] = build_queue(shuffled_games, start_idx)

    # Step 2: Shuffle bets within each game
    game_inner_shuffles = {}
    for gid in game_ids:
        inner = games[gid][:]
        random.shuffle(inner)
        game_inner_shuffles[gid] = inner

    # Assign unique starting bet per account within their first game
    game_cursors = {gid: 0 for gid in game_ids}
    account_starting_bets = {}
    for account in accounts:
        first_game = account_game_queues[account.id][0]
        cursor = game_cursors[first_game]
        inner = game_inner_shuffles[first_game]
        account_starting_bets[(account.id, first_game)] = cursor % len(inner)
        game_cursors[first_game] = cursor + 1

    # Execute: each account processes its game queue CONCURRENTLY
    async def _run_account_sgm_queue(account):
        account_results = []
        for game_idx, game_id in enumerate(account_game_queues[account.id]):
            game_bets = game_inner_shuffles[game_id]

            start_key = (account.id, game_id)
            start_bet_idx = account_starting_bets.get(start_key, 0)
            ordered_bets = build_queue(game_bets, start_bet_idx)

            for i, bet in enumerate(ordered_bets):
                if i > 0 or game_idx > 0:
                    delay = random.uniform(min_delay, max_delay)
                    await asyncio.sleep(delay)

                result = await process_chunk(
                    bet, account, state,
                    fetch_live_odds, get_kelly_stake, place_bet,
                )
                entry = {
                    "account": account.id,
                    "account_label": account.label,
                    "runner_id": bet.runner_id,
                    "bet_description": bet.bet_description,
                    "game_id": bet.game_id,
                    "result": result,
                }
                account_results.append(entry)
                if on_result:
                    on_result(entry)

        return account_results

    tasks = [_run_account_sgm_queue(account) for account in accounts]
    all_results = await asyncio.gather(*tasks)

    results = []
    for account_results in all_results:
        results.extend(account_results)

    return results


# ─── Retry System ─────────────────────────────────────────────────────────────

async def retry_failed_bet(
    bet: Bet,
    accounts: list[Account],
    state: RunState,
    fetch_live_odds: FetchLiveOdds,
    get_kelly_stake: GetKellyStake,
    place_bet: PlaceBet,
    get_confirmed_history: GetConfirmedHistory,
) -> list[dict]:
    """
    Safe re-placement for a failed bet.
    Uses bet history as source of truth — NOT in-memory state.
    """
    runner_id = bet.runner_id
    results = []

    # Step 1: pull confirmed history from all accounts
    history_placed = 0
    for account in accounts:
        history_placed += await get_confirmed_history(account, runner_id)

    logger.info("RETRY | runner=%s | history_placed=$%.0f", runner_id, history_placed)

    # Step 2: fetch live odds
    # Use first available account for odds fetch
    live_odds = None
    for account in accounts:
        live_odds = await fetch_live_odds(bet, account)
        if live_odds is not None:
            break

    if live_odds is None:
        logger.warning("RETRY ABORT | runner=%s | live odds unavailable", runner_id)
        return results

    if live_odds < bet.min_odds:
        logger.info("RETRY ABORT | runner=%s | live_odds=%.2f below min=%.2f",
                     runner_id, live_odds, bet.min_odds)
        return results

    # Step 3: recalculate Kelly at live odds
    kelly_target = min(
        get_kelly_stake(bet, bet.odds),
        get_kelly_stake(bet, live_odds),
    )

    # Step 4: apply ceiling — history counts toward cap
    remaining = kelly_target - history_placed
    if remaining <= 0:
        logger.info("RETRY SKIP | runner=%s | already fully covered ($%.0f >= $%.0f)",
                     runner_id, history_placed, kelly_target)
        return results

    # Step 5: round down, minimum check
    chunk = (remaining // 10) * 10
    if chunk < 10:
        logger.info("RETRY SKIP | runner=%s | remainder $%.0f too small", runner_id, remaining)
        return results

    # Step 6: place on first eligible account
    for account in accounts:
        if not account.enabled:
            continue
        balance = state.account_balances.get(account.id, 0)
        if balance < chunk:
            continue

        logger.info("RETRY PLACING | runner=%s acct=%s | chunk=$%.0f odds=%.2f",
                     runner_id, account.label, chunk, live_odds)

        result = await place_bet(account, bet, chunk, live_odds)

        entry = {
            "account": account.id,
            "account_label": account.label,
            "runner_id": runner_id,
            "bet_description": bet.bet_description,
            "game_id": bet.game_id,
            "result": result,
            "is_retry": True,
        }
        results.append(entry)

        if result.success:
            confirmed = result.confirmed_amount or chunk
            state.placed_totals[runner_id] = state.placed_totals.get(runner_id, 0) + confirmed
            state.account_balances[account.id] = balance - confirmed
            logger.info("RETRY CONFIRMED | runner=%s acct=%s | placed=$%.0f",
                         runner_id, account.label, confirmed)
        else:
            logger.warning("RETRY FAILED | runner=%s acct=%s | error=%s",
                           runner_id, account.label, result.error)

        break  # Only retry on one account

    return results


# ─── Main Entry Point ─────────────────────────────────────────────────────────

async def run_execution(
    bet_list: list[Bet],
    account_list: list[Account],
    fetch_live_odds: FetchLiveOdds,
    get_kelly_stake: GetKellyStake,
    place_bet: PlaceBet,
    on_result: Callable | None = None,
    min_delay: float = 2.0,
    max_delay: float = 6.0,
) -> dict:
    """
    Main entry point for the execution engine.

    Args:
        bet_list: All bets from CSV
        account_list: All enabled accounts
        fetch_live_odds: Callback to get live TAB odds for a bet
        get_kelly_stake: Callback to calculate Kelly stake at given odds
        place_bet: Callback to place a bet on an account
        on_result: Optional callback for real-time status updates

    Returns: {"regular": results, "sgm": results, "state": run_state}
    """
    state = RunState()

    # Snapshot balances at start of run
    for account in account_list:
        state.account_balances[account.id] = account.session.get("balance", 0)

    # Separate regular multis from SGMs
    regular_bets = [b for b in bet_list if not b.is_sgm]
    sgm_bets = [b for b in bet_list if b.is_sgm]

    logger.info("Execution starting: %d regular, %d SGM, %d accounts",
                len(regular_bets), len(sgm_bets), len(account_list))

    # Run both pipelines
    regular_results = await run_multis(
        regular_bets, account_list, state,
        fetch_live_odds, get_kelly_stake, place_bet, on_result,
        min_delay, max_delay,
    )

    sgm_results = await run_sgms(
        sgm_bets, account_list, state,
        fetch_live_odds, get_kelly_stake, place_bet, on_result,
        min_delay, max_delay,
    )

    logger.info("Execution complete: placed_totals=%s", state.placed_totals)

    return {
        "regular": regular_results,
        "sgm": sgm_results,
        "state": {
            "placed_totals": state.placed_totals,
            "allocated_totals": state.allocated_totals,
            "skipped_runners": {k: list(v) for k, v in state.skipped_runners.items()},
        },
    }

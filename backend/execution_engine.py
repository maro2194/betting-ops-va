"""
BetOps Execution Engine V3 — Anti-Detection, Dynamic Re-Pricing & Safe Re-Placement.

V3 improvements over V2:
  - $5 standard rounding (replaces $10 round-down for accurate Kelly staking)
  - Pre-bet dwell time (2-4s before placement, mimics human browsing)
  - Lease-based lock expiry (90s TTL, prevents deadlocks from API hangs)
  - $9.99 closure failsafe (remaining < $10 → tip complete, no wasted API calls)
  - Auto-sweep retry (failed tips retried in second pass after main run)
  - asyncio.shield (protects placement + balance update from VPS crashes)
  - 3-spin keep-alive (workers loop board 3x before shutting down)
  - SGM match batching (lock entire match, process all tips before moving on)

Architecture: MasterChecklist manages shared state. Workers are concurrent per-account
loops that take tips from the board. Lease locks prevent collisions. JIT Kelly calculation
ensures each account gets the right chunk based on current state.

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

# ─── Constants ───────────────────────────────────────────────────────────────

DWELL_MIN = 2.0      # Pre-bet dwell (human browsing simulation)
DWELL_MAX = 4.0
POST_BET_MIN = 2.0   # Post-bet delay
POST_BET_MAX = 6.0
SPIN_WAIT_MIN = 5.0  # Between worker spins
SPIN_WAIT_MAX = 10.0
LOCK_TTL = 90        # Lease lock expiry (seconds)
MAX_SPINS = 10       # Worker keep-alive spins (generous — real exit is "no remaining work")
MIN_BET = 10         # TAB minimum bet ($)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def standard_round_5(value: float) -> float:
    """Round to nearest $5 (arithmetic half-up rounding)."""
    return int(value / 5.0 + 0.5) * 5.0


def floor_round_5(value: float) -> float:
    """Floor to nearest $5 (never rounds up — used when capping)."""
    return (int(value) // 5) * 5


# ─── Canonical Runner ID ────────────────────────────────────────────────────

def normalize_runner_id(game_id: str, bet_description: str, is_sgm: bool) -> str:
    """
    Canonical runner key used for ALL tracking: placed_totals, history lookups, retries.
    MUST be called identically at every call site. Defined once here, imported everywhere.
    """
    desc = (bet_description or "").strip().lower()
    desc = re.sub(r'\s+', ' ', desc)
    desc = desc.replace(" / ", "/")
    desc = re.sub(r'\s*\+\s*', '+', desc)
    if is_sgm and game_id:
        return f"{game_id.strip().upper()}::{desc}"
    return desc


# ─── Data Types ──────────────────────────────────────────────────────────────

@dataclass
class Bet:
    """A single bet from the CSV."""
    bet_type: str
    game_id: str
    bet_description: str
    odds: float
    min_odds: float
    ev_pct: float
    units: float
    sport: str = ""
    competition: str = ""
    leg_odds: list[float] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def is_sgm(self) -> bool:
        return self.bet_type.upper() == "SGM"

    @property
    def runner_id(self) -> str:
        return normalize_runner_id(self.game_id, self.bet_description, self.is_sgm)


@dataclass
class Account:
    """A TAB account available for betting."""
    id: str
    label: str
    session: dict
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
    skipped: bool = False
    legs: list = field(default_factory=list)


# ─── V2 Backward Compat ─────────────────────────────────────────────────────

@dataclass
class RunState:
    """V2 backward-compatibility alias. V3 uses MasterChecklist internally."""
    placed_totals: dict = field(default_factory=dict)
    allocated_totals: dict = field(default_factory=dict)
    account_balances: dict = field(default_factory=dict)
    skipped_runners: dict = field(default_factory=dict)
    placed_by_account: dict = field(default_factory=dict)
    dead_runners: set = field(default_factory=set)
    locks: dict = field(default_factory=dict)


# ─── Callbacks (injected by engine_integration.py) ───────────────────────────

FetchLiveOdds = Callable[[Bet, Account], Awaitable[Optional[float]]]
GetKellyStake = Callable[[Bet, float], float]
PlaceBet = Callable[[Account, Bet, float, float], Awaitable[ChunkResult]]
GetConfirmedHistory = Callable[[Account, str], Awaitable[float]]


# ─── Tip State ───────────────────────────────────────────────────────────────

@dataclass
class TipState:
    """Per-tip tracking. Managed atomically by MasterChecklist."""
    target: Optional[float] = None
    placed_total: float = 0.0
    placed_accounts: set = field(default_factory=set)
    skipped_accounts: set = field(default_factory=set)
    status: str = "PENDING"  # PENDING → PLACED | RETRY_QUEUED → PLACED | DEAD


# ─── Master Checklist (Atomic State Manager) ────────────────────────────────

class MasterChecklist:
    """
    Centralized brain. All shared state mutations go through _state_lock.
    Workers are concurrent loops that take instructions from the Master.
    """

    def __init__(self, bets: list[Bet], accounts: list[Account]):
        self.bets = bets
        self.account_list = accounts
        self.tip_states: dict[str, TipState] = {}
        self.account_balances: dict[str, float] = {}
        self.account_exhausted: dict[str, bool] = {}

        for bet in bets:
            if bet.runner_id not in self.tip_states:
                self.tip_states[bet.runner_id] = TipState()

        self._leases: dict[str, tuple[float, str]] = {}
        self._state_lock = asyncio.Lock()

    # ── Lease Locks (unified TTL) ──────────────────────────────────────────

    async def acquire_lock(self, bet: Bet, account_id: str) -> bool:
        """Acquire lease. SGMs lock match, Multis lock tip. Non-blocking."""
        lock_key = f"match:{bet.game_id}" if bet.is_sgm else f"tip:{bet.runner_id}"
        now = time.time()
        async with self._state_lock:
            if lock_key in self._leases:
                expiry, owner = self._leases[lock_key]
                if now < expiry and owner != account_id:
                    return False
            self._leases[lock_key] = (now + LOCK_TTL, account_id)
            return True

    async def release_lock(self, bet: Bet, account_id: str):
        lock_key = f"match:{bet.game_id}" if bet.is_sgm else f"tip:{bet.runner_id}"
        async with self._state_lock:
            if lock_key in self._leases:
                _, owner = self._leases[lock_key]
                if owner == account_id:
                    del self._leases[lock_key]

    # ── JIT Chunk Calculation ──────────────────────────────────────────────

    async def calculate_chunk(
        self, runner_id: str, account_id: str,
        live_odds: float, get_kelly_stake: GetKellyStake, bet: Bet,
    ) -> Optional[float]:
        """
        Just-in-time chunk calc. Sets/updates Kelly target, returns this account's
        fair share. Returns stake, 0 if skip, or None if tip closed.
        """
        async with self._state_lock:
            state = self.tip_states.get(runner_id)
            if not state or state.status not in ("PENDING", "RETRY_QUEUED"):
                return None

            if account_id in state.placed_accounts or account_id in state.skipped_accounts:
                return None

            # Scout (first account) or ratchet down (odds dropped)
            original_target = get_kelly_stake(bet, bet.odds)
            live_target = get_kelly_stake(bet, live_odds)

            if state.target is None:
                state.target = original_target
            new_target = min(state.target, live_target)
            if new_target < state.target:
                state.target = new_target

            remaining = state.target - state.placed_total

            # $9.99 closure: remaining below min bet → done
            if remaining < MIN_BET:
                state.status = "PLACED"
                logger.info("CLOSED | runner=%s | $%.0f / $%.0f (remaining $%.2f < $%d)",
                            runner_id, state.placed_total, state.target, remaining, MIN_BET)
                return None

            # Eligible accounts
            eligible = [
                acc for acc in self.account_list
                if acc.id not in state.placed_accounts
                and acc.id not in state.skipped_accounts
                and not self.account_exhausted.get(acc.id, False)
            ]
            num_eligible = max(1, len(eligible))

            balance = self.account_balances.get(account_id, 0)
            if balance < MIN_BET:
                self.account_exhausted[account_id] = True
                state.skipped_accounts.add(account_id)
                self._check_closure(state, runner_id)
                return None

            # $5 standard rounding split
            chunk = standard_round_5(remaining / num_eligible)

            # Greedy: if split below min but tip can still fill → take larger share
            if chunk < MIN_BET and remaining >= MIN_BET:
                chunk = standard_round_5(min(remaining, balance))

            # Cap at remaining (floor to avoid over-allocation)
            if chunk > remaining:
                chunk = floor_round_5(remaining)

            # Cap at balance (floor)
            if chunk > balance:
                chunk = floor_round_5(balance)

            # Final min check
            if chunk < MIN_BET:
                state.skipped_accounts.add(account_id)
                self._check_closure(state, runner_id)
                return 0

            return chunk

    def _check_closure(self, state: TipState, runner_id: str):
        """Close tip if no eligible accounts remain. Must hold _state_lock."""
        eligible = [
            acc for acc in self.account_list
            if acc.id not in state.placed_accounts
            and acc.id not in state.skipped_accounts
            and not self.account_exhausted.get(acc.id, False)
        ]
        if not eligible:
            state.status = "PLACED"
            logger.info("CLOSED | runner=%s | $%.0f / $%.0f (no eligible accounts)",
                        runner_id, state.placed_total, state.target or 0)

    # ── State Updates ──────────────────────────────────────────────────────

    async def handle_odds_below_min(self, runner_id: str, account_id: str):
        """Main run → RETRY_QUEUED. Sweep run → skip account."""
        async with self._state_lock:
            state = self.tip_states.get(runner_id)
            if not state:
                return
            if state.status == "PENDING":
                state.status = "RETRY_QUEUED"
                state.skipped_accounts.clear()
            else:
                state.skipped_accounts.add(account_id)
                self._check_closure(state, runner_id)

    async def record_placement(self, runner_id: str, account_id: str, amount_placed: float):
        """Record successful bet. Updates balance, checks closure."""
        async with self._state_lock:
            self.account_balances[account_id] -= amount_placed
            if self.account_balances[account_id] < MIN_BET:
                self.account_exhausted[account_id] = True

            state = self.tip_states[runner_id]
            state.placed_total += amount_placed
            state.placed_accounts.add(account_id)

            remaining = (state.target or 0) - state.placed_total
            if remaining < MIN_BET:
                state.status = "PLACED"
                logger.info("CLOSED | runner=%s | $%.0f / $%.0f (target hit)",
                            runner_id, state.placed_total, state.target or 0)
            else:
                self._check_closure(state, runner_id)

    async def record_skip(self, runner_id: str, account_id: str):
        """Record that an account couldn't place on this tip."""
        async with self._state_lock:
            state = self.tip_states.get(runner_id)
            if state:
                state.skipped_accounts.add(account_id)
                self._check_closure(state, runner_id)


# ─── Per-Bet Processing ─────────────────────────────────────────────────────

def _make_entry(account: Account, bet: Bet, result: ChunkResult) -> dict:
    """Build result entry in V2-compatible format."""
    return {
        "account": account.id,
        "account_label": account.label,
        "runner_id": bet.runner_id,
        "bet_description": bet.bet_description,
        "game_id": bet.game_id,
        "result": result,
    }


async def process_single_tip(
    account: Account,
    bet: Bet,
    master: MasterChecklist,
    fetch_live_odds: FetchLiveOdds,
    get_kelly_stake: GetKellyStake,
    place_bet_fn: PlaceBet,
) -> Optional[dict]:
    """
    Full per-bet flow for one account on one tip:
    1. Fetch live odds → 2. Min odds gate → 3. Pre-bet dwell →
    4. JIT chunk calc → 5. Shielded placement → 6. Post-bet delay.
    """
    runner_id = bet.runner_id

    # Step 1: Fetch live odds
    live_odds = await fetch_live_odds(bet, account)
    if live_odds is None:
        return _make_entry(account, bet,
                           ChunkResult(success=False, error="Live odds unavailable"))

    # Step 2: Min odds gate
    if live_odds < bet.min_odds:
        logger.info("ODDS_DROP | runner=%s acct=%s | live=%.2f < min=%.2f",
                     runner_id, account.label, live_odds, bet.min_odds)
        await master.handle_odds_below_min(runner_id, account.id)
        return _make_entry(account, bet, ChunkResult(
            success=False, skipped=True,
            error=f"Odds drifted: {live_odds:.2f} < min {bet.min_odds:.2f}"))

    # Step 3: Pre-bet dwell (human browsing simulation)
    await asyncio.sleep(random.uniform(DWELL_MIN, DWELL_MAX))

    # Step 4: JIT chunk calculation
    chunk = await master.calculate_chunk(
        runner_id, account.id, live_odds, get_kelly_stake, bet)
    if chunk is None:
        return None  # Tip closed or account already processed
    if chunk < MIN_BET:
        return _make_entry(account, bet, ChunkResult(
            success=False, skipped=True, error=f"Stake ${chunk:.0f} below ${MIN_BET} min"))

    # Step 5: Shielded placement + state update
    logger.info("PLACING | runner=%s acct=%s | $%.0f @ %.2f",
                 runner_id, account.label, chunk, live_odds)

    result = ChunkResult(success=False, error="Execution interrupted")
    try:
        async def _shielded():
            nonlocal result
            result = await place_bet_fn(account, bet, chunk, live_odds)
            if result.success:
                confirmed = result.confirmed_amount or chunk
                await master.record_placement(runner_id, account.id, confirmed)
                logger.info("CONFIRMED | runner=%s acct=%s | $%.0f",
                             runner_id, account.label, confirmed)
            else:
                await master.record_skip(runner_id, account.id)
                logger.warning("FAILED | runner=%s acct=%s | %s",
                                runner_id, account.label, result.error)

        await asyncio.shield(_shielded())
    except asyncio.CancelledError:
        # Shielded coroutine is still running — don't re-raise until it's done.
        # Return whatever result we have so the bet isn't "lost" from the response.
        logger.warning("SHIELD | runner=%s | cancelled, returning partial result", runner_id)
        return _make_entry(account, bet, result)

    # Step 6: Post-bet delay
    await asyncio.sleep(random.uniform(POST_BET_MIN, POST_BET_MAX))

    return _make_entry(account, bet, result)


# ─── Worker Loop ─────────────────────────────────────────────────────────────

async def worker_loop(
    account: Account,
    master: MasterChecklist,
    fetch_live_odds: FetchLiveOdds,
    get_kelly_stake: GetKellyStake,
    place_bet_fn: PlaceBet,
    on_result: Callable | None = None,
    sweep_run: bool = False,
) -> list[dict]:
    """
    Per-account worker. Loops through the tip board placing bets.
    3-spin keep-alive: shuts down after 3 consecutive empty passes.
    Random starting index for anti-detection staggering.
    """
    results = []
    total_tips = len(master.bets)
    if total_tips == 0 or master.account_exhausted.get(account.id, False):
        return results

    target_status = "RETRY_QUEUED" if sweep_run else "PENDING"
    spin_count = 0
    start_index = random.randint(0, total_tips - 1)

    while spin_count < MAX_SPINS:
        if master.account_exhausted.get(account.id, False):
            break

        # Any tips still need work?
        has_work = any(
            (ts := master.tip_states.get(b.runner_id)) and ts.status == target_status
            for b in master.bets
        )
        if not has_work:
            break

        bets_placed = 0

        for offset in range(total_tips):
            if master.account_exhausted.get(account.id, False):
                break

            idx = (start_index + offset) % total_tips
            bet = master.bets[idx]
            ts = master.tip_states.get(bet.runner_id)

            # Quick pre-checks (no lock needed — stale reads are safe)
            if not ts or ts.status != target_status:
                continue
            if account.id in ts.placed_accounts or account.id in ts.skipped_accounts:
                continue

            # Acquire lease (non-blocking — skip if another account holds it)
            if not await master.acquire_lock(bet, account.id):
                continue

            try:
                if bet.is_sgm:
                    # SGM match batch: lock match, process ALL tips for it
                    match_bets = [
                        b for b in master.bets
                        if b.game_id == bet.game_id
                        and (mts := master.tip_states.get(b.runner_id))
                        and mts.status == target_status
                        and account.id not in mts.placed_accounts
                        and account.id not in mts.skipped_accounts
                    ]
                    for m_bet in match_bets:
                        if master.account_exhausted.get(account.id, False):
                            break
                        entry = await process_single_tip(
                            account, m_bet, master,
                            fetch_live_odds, get_kelly_stake, place_bet_fn)
                        if entry:
                            results.append(entry)
                            if on_result:
                                on_result(entry)
                            if entry["result"].success:
                                bets_placed += 1
                else:
                    entry = await process_single_tip(
                        account, bet, master,
                        fetch_live_odds, get_kelly_stake, place_bet_fn)
                    if entry:
                        results.append(entry)
                        if on_result:
                            on_result(entry)
                        if entry["result"].success:
                            bets_placed += 1
            finally:
                await master.release_lock(bet, account.id)

        if bets_placed == 0:
            # Check if there's still work this account could do
            can_still_work = any(
                (ts := master.tip_states.get(b.runner_id))
                and ts.status == target_status
                and account.id not in ts.placed_accounts
                and account.id not in ts.skipped_accounts
                for b in master.bets
            )
            if not can_still_work:
                break  # No remaining work for this account
            spin_count += 1
            if spin_count < MAX_SPINS:
                await asyncio.sleep(random.uniform(SPIN_WAIT_MIN, SPIN_WAIT_MAX))
        else:
            spin_count = 0
            start_index = random.randint(0, total_tips - 1)

    return results


# ─── Main Entry Point ────────────────────────────────────────────────────────

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
    Main entry point. Same API as V2.
    Runs main pass (PENDING tips), then auto-sweep (RETRY_QUEUED tips).

    Returns: {"regular": [...], "sgm": [...], "state": {...}}
    """
    master = MasterChecklist(bet_list, account_list)

    # Snapshot balances (default 10000 if unknown — TAB enforces real limits)
    for account in account_list:
        master.account_balances[account.id] = account.session.get("balance", 0) or 10000
        master.account_exhausted[account.id] = False

    logger.info("V3 Engine starting: %d tips, %d accounts", len(bet_list), len(account_list))

    # ── Main Run (PENDING tips) ──
    tasks = [
        worker_loop(acc, master, fetch_live_odds, get_kelly_stake, place_bet, on_result)
        for acc in account_list
    ]
    all_main = await asyncio.gather(*tasks)
    main_results = [entry for acct_results in all_main for entry in acct_results]

    # ── Sweep Run (RETRY_QUEUED tips) ──
    retry_runners = [
        rid for rid, ts in master.tip_states.items()
        if ts.status == "RETRY_QUEUED"
    ]
    sweep_results = []

    if retry_runners:
        logger.info("V3 Sweep: %d tips queued for retry", len(retry_runners))

        # Clear skipped accounts so everyone gets another shot
        for rid in retry_runners:
            master.tip_states[rid].skipped_accounts.clear()

        sweep_accounts = [
            acc for acc in account_list
            if not master.account_exhausted.get(acc.id, False)
        ]
        if sweep_accounts:
            tasks = [
                worker_loop(acc, master, fetch_live_odds, get_kelly_stake, place_bet,
                            on_result, sweep_run=True)
                for acc in sweep_accounts
            ]
            all_sweep = await asyncio.gather(*tasks)
            sweep_results = [entry for acct_results in all_sweep for entry in acct_results]

    # ── Build V2-compatible response ──
    all_entries = main_results + sweep_results
    regular = [e for e in all_entries if "::" not in e.get("runner_id", "")]
    sgm = [e for e in all_entries if "::" in e.get("runner_id", "")]

    placed = sum(1 for e in all_entries if e.get("result") and e["result"].success)
    failed = sum(1 for e in all_entries
                 if e.get("result") and not e["result"].success and not e["result"].skipped)
    skipped = sum(1 for e in all_entries if e.get("result") and e["result"].skipped)

    logger.info("V3 complete: %d placed, %d failed, %d skipped", placed, failed, skipped)

    return {
        "regular": regular,
        "sgm": sgm,
        "state": {
            "placed_totals": {
                rid: ts.placed_total
                for rid, ts in master.tip_states.items() if ts.placed_total > 0
            },
            "allocated_totals": {
                rid: ts.target or 0
                for rid, ts in master.tip_states.items() if ts.target
            },
            "skipped_runners": {
                rid: list(ts.skipped_accounts)
                for rid, ts in master.tip_states.items()
                if ts.status in ("DEAD", "RETRY_QUEUED")
            },
        },
    }


# ─── V2 Compat: retry_failed_bet ────────────────────────────────────────────

async def retry_failed_bet(
    bet: Bet,
    accounts: list[Account],
    state: RunState,
    fetch_live_odds: FetchLiveOdds,
    get_kelly_stake: GetKellyStake,
    place_bet: PlaceBet,
    get_confirmed_history: GetConfirmedHistory,
) -> list[dict]:
    """V2 compat — runs a mini V3 execution for a single failed bet."""
    result = await run_execution([bet], accounts, fetch_live_odds, get_kelly_stake, place_bet)
    return result.get("regular", []) + result.get("sgm", [])

"""Integration test: exercise MasterChecklist.calculate_chunk with realistic flow.

Does not place real bets — just simulates the state machine.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from execution_engine import MasterChecklist, Bet, Account, MIN_BET, normalize_runner_id


def mkbet(game_id="G1", desc="H1", odds=3.0) -> Bet:
    return Bet(
        bet_type="racing", game_id=game_id, bet_description=desc,
        odds=odds, min_odds=odds * 0.9, ev_pct=0.1, units=5.0,
    )


def mkacc(id: str) -> Account:
    return Account(id=id, label=id, session={}, enabled=True)


def kelly_flat(target: float):
    """Mock Kelly: always returns the same target."""
    return lambda bet, odds: target


async def simulate(label: str, bet: Bet, accounts: list[tuple], target: float,
                   skip_account_ids: list[str] = None):
    """accounts = [(id, balance), ...]. Simulate placements in order."""
    print(f"\n=== {label} ===")
    accs = [mkacc(aid) for aid, _bal in accounts]
    bals = {aid: bal for aid, bal in accounts}
    mc = MasterChecklist([bet], accs)
    mc.account_balances = dict(bals)
    # Ensure runner_id matches the Bet property
    runner_id = bet.runner_id if hasattr(bet, "runner_id") else normalize_runner_id(
        bet.game_id, bet.bet_description, bet.is_sgm
    )

    skip_ids = set(skip_account_ids or [])
    get_kelly = kelly_flat(target)

    placements = []
    for acc in accs:
        chunk = await mc.calculate_chunk(
            runner_id, acc.id, bet.odds, get_kelly, bet
        )
        bal = bals[acc.id]
        if chunk is None:
            placements.append((acc.id, bal, None, "closed/null"))
            continue
        if chunk == 0:
            placements.append((acc.id, bal, 0, "skipped"))
            continue
        if acc.id in skip_ids:
            placements.append((acc.id, bal, chunk, "fail-sim"))
        else:
            await mc.record_placement(runner_id, acc.id, chunk)
            placements.append((acc.id, bal, chunk, "placed"))

    state = mc.tip_states[runner_id]
    total = sum(p[2] for p in placements if isinstance(p[2], (int, float)) and p[2] > 0)
    print(f"  target=${target}  placed=${total}  status={state.status}")
    for pid, bal, chunk, status in placements:
        cstr = f"${chunk}" if isinstance(chunk, (int, float)) else "—"
        print(f"    {pid} (bal ${bal}) -> {cstr}  [{status}]")

    non_capped = [p[2] for p in placements
                  if isinstance(p[2], (int, float)) and p[2] > 0
                  and p[1] > p[2] * 1.5]
    if non_capped and len(non_capped) >= 2:
        ratio = max(non_capped) / min(non_capped)
        print(f"  non-capped spread: {ratio:.2f}x (target ≤ 3x)")


async def main():
    bet = mkbet()
    await simulate("Target $215, 3 accs x $500", bet,
                   [("A", 500), ("B", 500), ("C", 500)], target=215)
    await simulate("Target $215, 2 accs x $500 (former $200/$15 pain)", bet,
                   [("A", 500), ("B", 500)], target=215)
    await simulate("Target $215, 4 accs, D only $15", bet,
                   [("A", 500), ("B", 500), ("C", 500), ("D", 15)], target=215)
    await simulate("Target $150, 2 accs $500/$15 (balance-forced)", bet,
                   [("A", 500), ("D", 15)], target=150)
    await simulate("Target $45, 2 accs x $500 (small bet)", bet,
                   [("A", 500), ("B", 500)], target=45)
    await simulate("Target $400, 5 accs x $500", bet,
                   [(f"A{i}", 500) for i in range(5)], target=400)


if __name__ == "__main__":
    asyncio.run(main())

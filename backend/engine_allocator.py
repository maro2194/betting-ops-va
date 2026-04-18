"""CSB V4 stake allocator — replaces the JIT `remaining / num_eligible` logic
with a pre-computed plan that gives natural variance, a 3× max-ratio ceiling,
and graceful balance-cap redistribution.

Pure function: no side effects, deterministic-with-seed. Unit-testable.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

MIN_BET = 5
STEP = 5                # round to nearest $5
MAX_RATIO = 3.0         # biggest chunk ≤ 3× smallest chunk (excluding balance-forced)
VARIANCE_PCT = 0.5      # each chunk randomised ±50% around baseline before normalising


@dataclass
class Acct:
    id: str
    balance: float


def _round_step(x: float) -> int:
    """Round to nearest STEP, returning an int."""
    return int(round(x / STEP) * STEP)


def _floor_step(x: float) -> int:
    return int((x // STEP) * STEP)


def allocate_plan(
    target: float,
    accounts: list[Acct],
    *,
    seed: int | None = None,
    variance_pct: float = VARIANCE_PCT,
    max_ratio: float = MAX_RATIO,
    min_bet: int = MIN_BET,
) -> dict[str, int]:
    """Return {account_id: chunk} summing to ~target, respecting balances.

    Rules:
      1. Baseline = target / n.  Each chunk is a random weight × baseline.
      2. Weights normalised so sum = target.
      3. Chunks rounded to $5 multiples.
      4. If a chunk > balance → floored to balance, overflow redistributed
         proportionally to accounts with headroom.
      5. Non-balance-capped chunks enforced to have max/min ≤ max_ratio.
      6. Chunks < min_bet → set to 0 (account skipped).
      7. Residual rounding error assigned to account with most headroom.
    """
    rng = random.Random(seed)
    n = len(accounts)
    if n == 0 or target < min_bet:
        return {a.id: 0 for a in accounts}

    baseline = target / n

    # Step 1: draw a weight per account in [1-variance, 1+variance]
    weights = {a.id: rng.uniform(1 - variance_pct, 1 + variance_pct) for a in accounts}
    total_w = sum(weights.values())
    raw = {a.id: (weights[a.id] / total_w) * target for a in accounts}

    # Step 2: round to $5, track balance caps
    plan: dict[str, int] = {}
    overflow = 0.0
    capped_ids: set[str] = set()
    for a in accounts:
        chunk = _round_step(raw[a.id])
        if chunk > a.balance:
            chunk = _floor_step(a.balance)
            capped_ids.add(a.id)
            overflow += raw[a.id] - chunk  # (not real $, just excess capacity)
        plan[a.id] = chunk

    # Step 3: redistribute overflow across non-capped, non-exhausted accounts
    if overflow > min_bet / 2:
        recipients = [a for a in accounts
                      if a.id not in capped_ids and plan[a.id] < _floor_step(a.balance)]
        while overflow >= min_bet and recipients:
            # add $5 to the recipient with most remaining headroom
            best = max(recipients, key=lambda a: a.balance - plan[a.id])
            headroom = _floor_step(best.balance) - plan[best.id]
            if headroom < min_bet:
                recipients.remove(best)
                continue
            plan[best.id] += min_bet
            overflow -= min_bet

    # Step 4: enforce max_ratio on non-capped chunks
    non_capped = [a for a in accounts
                  if a.id not in capped_ids and plan[a.id] >= min_bet]
    if non_capped:
        chunks = sorted(plan[a.id] for a in non_capped)
        lo, hi = chunks[0], chunks[-1]
        if lo > 0 and hi / lo > max_ratio:
            # Clamp the top chunks down to lo * max_ratio, redistribute
            ceiling = int((lo * max_ratio) // min_bet) * min_bet
            excess = 0
            for a in non_capped:
                if plan[a.id] > ceiling:
                    excess += plan[a.id] - ceiling
                    plan[a.id] = ceiling
            # push excess into accounts below ceiling
            under = [a for a in non_capped if plan[a.id] < ceiling and a.balance >= plan[a.id] + min_bet]
            while excess >= min_bet and under:
                best = max(under, key=lambda a: a.balance - plan[a.id])
                if _floor_step(best.balance) - plan[best.id] < min_bet:
                    under.remove(best); continue
                plan[best.id] += min_bet
                excess -= min_bet

    # Step 5: skip sub-min chunks (account gets $0 — caller will skip it)
    for aid, v in list(plan.items()):
        if v < min_bet:
            plan[aid] = 0

    # Step 6: handle rounding residue to hit exact target.
    # Adjust in STEP-sized increments (may be smaller than min_bet) so a $5
    # residue can still be absorbed by an existing chunk that's already >= min_bet.
    total = sum(plan.values())
    residue = _round_step(target - total)
    while residue != 0:
        by_headroom = sorted(
            [a for a in accounts if plan[a.id] >= min_bet],
            key=lambda a: a.balance - plan[a.id],
            reverse=(residue > 0),
        )
        moved = False
        for a in by_headroom:
            if residue > 0:
                headroom = _floor_step(a.balance) - plan[a.id]
                if headroom >= STEP:
                    plan[a.id] += STEP
                    residue -= STEP
                    moved = True
                    break
            else:
                if plan[a.id] - STEP >= min_bet:
                    plan[a.id] -= STEP
                    residue += STEP
                    moved = True
                    break
        if not moved:
            break  # can't place the residue anywhere — accept sub-STEP shortfall

    return plan


# ──────────────────────────── CLI demo ───────────────────────────────────

if __name__ == "__main__":
    SCENARIOS = [
        ("Target $215, 3 accs all $500",
         215, [Acct(f"A{i}", 500) for i in range(3)]),
        ("Target $215, 2 accs both $500 (former $200/$15 pain case)",
         215, [Acct("A", 500), Acct("B", 500)]),
        ("Target $215, 4 accs one low (D=$15)",
         215, [Acct("A", 500), Acct("B", 500), Acct("C", 500), Acct("D", 15)]),
        ("Target $150, 2 accs $500/$15 (balance-forced)",
         150, [Acct("A", 500), Acct("D", 15)]),
        ("Target $400, 5 accs all $500",
         400, [Acct(f"A{i}", 500) for i in range(5)]),
        ("Target $110, 3 accs all $500",
         110, [Acct(f"A{i}", 500) for i in range(3)]),
        ("Target $45, 2 accs all $500 (small bet)",
         45, [Acct("A", 500), Acct("B", 500)]),
    ]

    for label, tgt, accs in SCENARIOS:
        print(f"\n=== {label} ===")
        for seed in (1, 2, 3):
            plan = allocate_plan(tgt, accs, seed=seed)
            total = sum(plan.values())
            ratio = (max(plan.values()) / min(v for v in plan.values() if v > 0)
                     if any(plan.values()) else 0)
            nz = [plan[a.id] for a in accs if plan[a.id] > 0]
            all_ = [plan[a.id] for a in accs]
            print(f"  seed={seed}: {all_}  total=${total}  ratio={ratio:.1f}×")

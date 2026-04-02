"""
liability_split.py — Shadow's liability auto-split logic for multi-account betting.

Splits any bet whose liability (profit exposure) exceeds a configured cap across
multiple accounts, distributing stake evenly (with remainder chunks going to the
first N bets so nothing is lost to rounding).

Liability = (odds - 1) * stake
"""
import math
import logging
from typing import Optional

logger = logging.getLogger("liability_split")


# ─── Public API ───────────────────────────────────────────────────────────────

def needs_splitting(odds: float, stake: float, max_liability: float) -> bool:
    """Return True if this bet's liability exceeds the cap.

    Args:
        odds:           Decimal odds for the bet (e.g. 4.0).
        stake:          Total stake in dollars.
        max_liability:  Maximum allowed liability per account in dollars.

    Returns:
        True when (odds - 1) * stake > max_liability.

    Example:
        >>> needs_splitting(4.0, 300.0, 600.0)
        True   # liability = 3 * 300 = 900 > 600
        >>> needs_splitting(2.0, 100.0, 600.0)
        False  # liability = 1 * 100 = 100 <= 600
    """
    return (odds - 1) * stake > max_liability


def split_bets_by_liability(
    bets: list[dict],
    num_accounts: int,
    unit_size: float = 10.0,
    max_liability: float = 600.0,
    rounding_increment: float = 10.0,
) -> list[dict]:
    """Split bets that exceed max_liability across multiple accounts.

    Iterates over each bet, computes its total stake from ``units * unit_size``,
    then determines how many account slots are needed to keep every individual
    stake within the liability cap.  Stake is distributed as evenly as possible
    in ``rounding_increment`` chunks; any remainder (from integer division) is
    spread one chunk at a time across the first N splits.

    Args:
        bets:               List of bet dicts.  Each dict must contain at least:
                              - ``odds``  (float) decimal odds
                              - ``units`` (float) number of units to bet
                            Optional fields (passed through unchanged):
                              legs, ev_pct, min_odds, game_id, bet_type, stake, …
        num_accounts:       Number of available accounts.  Splits are capped at
                            this value — we cannot place more bets than we have
                            accounts.
        unit_size:          Dollar value of one unit (default $10).
        max_liability:      Maximum liability per account in dollars (default $600).
        rounding_increment: Stake granularity in dollars (default $10).  Stakes
                            are always multiples of this value.

    Returns:
        Expanded list of bet dicts.  Bets that do not need splitting are returned
        as-is (with split_group / split_index / split_total added for uniformity).
        Bets that are split appear as multiple entries, each with an adjusted
        ``stake`` field.

    Added fields on every returned bet:
        split_group (int):  All splits from the same original bet share this
                            incrementing group counter (0, 1, 2, …).
        split_index (int):  Position within the group (0-based).
        split_total (int):  Total number of splits in this group.

    Split algorithm:
        1. total_stake = units * unit_size
        2. max_stake_per_bet = floor(max_liability / (odds - 1)
                               / rounding_increment) * rounding_increment
        3. num_splits = max(1, ceil(total_stake / max_stake_per_bet))
        4. Cap num_splits at num_accounts.
        5. base = floor(total_stake / num_splits / rounding_increment)
                  * rounding_increment
        6. remainder_chunks = round((total_stake - base * num_splits)
                                    / rounding_increment)
        7. First remainder_chunks splits get (base + rounding_increment),
           the rest get base.

    Example:
        Bet: 2 units @ $100/unit = $200, odds 5.0
          liability if placed as one: (5-1)*200 = $800 > $600
          max_stake_per_bet = floor(600/4/10)*10 = $150
          num_splits = ceil(200/150) = 2
          base = floor(200/2/10)*10 = $100
          remainder_chunks = round((200 - 100*2)/10) = 0
          → split A: $100, split B: $100
    """
    if num_accounts < 1:
        raise ValueError("num_accounts must be >= 1")
    if rounding_increment <= 0:
        raise ValueError("rounding_increment must be > 0")
    if max_liability <= 0:
        raise ValueError("max_liability must be > 0")

    result: list[dict] = []
    group_counter = 0

    for bet in bets:
        odds: float = float(bet["odds"])
        units: float = float(bet.get("units", 0))
        total_stake: float = units * unit_size

        # Bets with odds <= 1.0 cannot have meaningful liability; pass through.
        if odds <= 1.0 or total_stake <= 0:
            result.append({
                **bet,
                "stake": total_stake,
                "split_group": group_counter,
                "split_index": 0,
                "split_total": 1,
            })
            group_counter += 1
            continue

        if not needs_splitting(odds, total_stake, max_liability):
            # No split required — emit single entry with split metadata.
            result.append({
                **bet,
                "stake": total_stake,
                "split_group": group_counter,
                "split_index": 0,
                "split_total": 1,
            })
            group_counter += 1
            continue

        # ── Compute split parameters ──────────────────────────────────────────
        profit_multiplier = odds - 1.0

        # Maximum stake per account that keeps liability within cap,
        # rounded DOWN to the nearest rounding_increment.
        max_stake_per_bet = (
            math.floor((max_liability / profit_multiplier) / rounding_increment)
            * rounding_increment
        )

        if max_stake_per_bet <= 0:
            # Odds so high that even one increment exceeds cap; use one split
            # and accept that we slightly breach — caller's responsibility.
            logger.warning(
                "Bet odds %.2f so high that no stake multiple of %.1f fits "
                "within max_liability %.1f; emitting as single unsplit bet.",
                odds, rounding_increment, max_liability,
            )
            result.append({
                **bet,
                "stake": total_stake,
                "split_group": group_counter,
                "split_index": 0,
                "split_total": 1,
            })
            group_counter += 1
            continue

        num_splits = max(1, math.ceil(total_stake / max_stake_per_bet))
        # Cap at available accounts.
        num_splits = min(num_splits, num_accounts)

        # ── Distribute stake evenly with remainder chunks ─────────────────────
        base = (
            math.floor(total_stake / num_splits / rounding_increment)
            * rounding_increment
        )
        remainder_chunks = round((total_stake - base * num_splits) / rounding_increment)

        logger.debug(
            "Splitting bet (odds=%.2f, stake=%.2f) into %d parts: "
            "base=%.2f, remainder_chunks=%d",
            odds, total_stake, num_splits, base, remainder_chunks,
        )

        for i in range(num_splits):
            split_stake = base + (rounding_increment if i < remainder_chunks else 0)
            result.append({
                **bet,
                "stake": split_stake,
                "split_group": group_counter,
                "split_index": i,
                "split_total": num_splits,
            })

        group_counter += 1

    return result


# ─── Helpers ──────────────────────────────────────────────────────────────────

def summarise_splits(split_bets: list[dict]) -> None:
    """Print a human-readable summary of split_bets to stdout.

    Intended for quick inspection / debugging.  Groups bets by split_group and
    shows stake, odds, liability, and split metadata.
    """
    if not split_bets:
        print("(no bets)")
        return

    groups: dict[int, list[dict]] = {}
    for b in split_bets:
        groups.setdefault(b["split_group"], []).append(b)

    for gid, members in sorted(groups.items()):
        first = members[0]
        total = sum(m["stake"] for m in members)
        print(
            f"Group {gid} | odds={first['odds']:.2f} | "
            f"splits={first['split_total']} | total_stake=${total:.2f}"
        )
        for m in members:
            liability = (m["odds"] - 1) * m["stake"]
            print(
                f"  [{m['split_index']}] stake=${m['stake']:.2f}  "
                f"liability=${liability:.2f}"
            )


# ─── __main__ demo / smoke-test ───────────────────────────────────────────────

if __name__ == "__main__":
    import json

    def _bet(odds: float, units: float, **extra) -> dict:
        return {"odds": odds, "units": units, "legs": [], "bet_type": "SGM", **extra}

    # ── Test 1: no split needed ───────────────────────────────────────────────
    # liability = (2.0-1)*50 = $50, well under $600
    bets_1 = [_bet(2.0, 5.0, game_id="G001")]  # 5u * $10 = $50 stake
    out_1 = split_bets_by_liability(bets_1, num_accounts=5)
    assert len(out_1) == 1, f"Expected 1, got {len(out_1)}"
    assert out_1[0]["split_total"] == 1
    assert out_1[0]["stake"] == 50.0
    print("Test 1 PASS — no split (liability within cap)")
    summarise_splits(out_1)

    print()

    # ── Test 2: example from docstring (odds 4.0, $300 total) ─────────────────
    # liability = (4-1)*300 = $900 > $600 → split needed
    # max_stake_per_bet = floor(600/3/10)*10 = $200
    # num_splits = ceil(300/200) = 2
    # base = floor(300/2/10)*10 = $150, remainder_chunks = 0
    # → $150 + $150
    bets_2 = [_bet(4.0, 30.0, game_id="G002")]  # 30u * $10 = $300
    out_2 = split_bets_by_liability(bets_2, num_accounts=5)
    assert len(out_2) == 2, f"Expected 2, got {len(out_2)}"
    assert out_2[0]["stake"] == 150.0
    assert out_2[1]["stake"] == 150.0
    assert all(b["split_total"] == 2 for b in out_2)
    print("Test 2 PASS — 2-way split, $150 each (odds 4.0, $300 total)")
    summarise_splits(out_2)

    print()

    # ── Test 3: remainder distribution ────────────────────────────────────────
    # odds 3.0, total_stake = $250
    # liability = 2*250 = $500 <= $600 → actually no split!
    # Use $350 total instead: liability = 2*350 = $700 > $600
    # max_stake_per_bet = floor(600/2/10)*10 = $300
    # num_splits = ceil(350/300) = 2
    # base = floor(350/2/10)*10 = $170, remainder_chunks = round((350-340)/10) = 1
    # → $180 + $170
    bets_3 = [_bet(3.0, 35.0, game_id="G003")]  # 35u * $10 = $350
    out_3 = split_bets_by_liability(bets_3, num_accounts=5)
    assert len(out_3) == 2, f"Expected 2, got {len(out_3)}"
    assert out_3[0]["stake"] == 180.0, f"Expected 180, got {out_3[0]['stake']}"
    assert out_3[1]["stake"] == 170.0, f"Expected 170, got {out_3[1]['stake']}"
    total_3 = sum(b["stake"] for b in out_3)
    assert total_3 == 350.0, f"Stake total mismatch: {total_3}"
    print("Test 3 PASS — remainder distributed: $180 + $170 (total $350)")
    summarise_splits(out_3)

    print()

    # ── Test 4: capped at num_accounts ────────────────────────────────────────
    # Very high odds, large stake — would need many splits but only 3 accounts
    # odds 10.0, stake $5000: liability per $60 chunk = 9*60=$540, need ceil(5000/60)=84 splits
    # but capped at 3
    bets_4 = [_bet(10.0, 500.0, game_id="G004")]  # 500u * $10 = $5000
    out_4 = split_bets_by_liability(bets_4, num_accounts=3)
    assert len(out_4) == 3, f"Expected 3 (capped), got {len(out_4)}"
    total_4 = sum(b["stake"] for b in out_4)
    assert total_4 == 5000.0, f"Stake total mismatch: {total_4}"
    print("Test 4 PASS — capped at 3 accounts, total preserved ($5000)")
    summarise_splits(out_4)

    print()

    # ── Test 5: multiple bets, mixed splitting ────────────────────────────────
    mixed = [
        _bet(2.0, 5.0, game_id="CHEAP"),   # $50, no split
        _bet(4.0, 30.0, game_id="BIG"),    # $300, 2-way split
    ]
    out_5 = split_bets_by_liability(mixed, num_accounts=5)
    assert len(out_5) == 3, f"Expected 3, got {len(out_5)}"
    groups = {b["game_id"]: b["split_total"] for b in out_5}
    assert groups["CHEAP"] == 1
    assert groups["BIG"] == 2
    print("Test 5 PASS — mixed batch: 1 passthrough + 2-way split")
    summarise_splits(out_5)

    print()
    print("All tests passed.")

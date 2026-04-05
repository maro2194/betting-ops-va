"""CSV parsing and multi-bookie bet orchestration."""
import asyncio
import csv
import io
import logging
import random
import uuid
from dataclasses import dataclass

from platforms.registry import get_platform_and_brand, get_client, is_supported, normalize_bookmaker
from session_manager import session_manager, _session_key
from multi_database import (
    find_account_by_initials_brand, update_csv_row, update_batch_summary,
    save_multi_bet,
)

logger = logging.getLogger(__name__)


@dataclass
class CsvBetRow:
    date: str
    track: str
    race: int
    horse: str
    units: str
    odds: float  # parsed from "$2.80"
    owner: str
    initials: str
    bookmaker: str
    stake_type: str  # "cash", "token", "promo"
    stake: float
    promotion: str
    target_stake: str
    fully_allocated: str
    # Sports fields (optional — empty for racing rows)
    row_type: str = "racing"  # "racing" or "sports"
    event: str = ""
    bet_desc: str = ""
    sport: str = ""
    min_odds: float = 0.0


def parse_racing_csv(csv_text: str) -> list[CsvBetRow]:
    """Parse a unified allocation CSV into structured rows.
    Supports both racing and sports rows via optional 'Type' column.
    Handles quoted fields, strips $ from odds, converts types."""
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = []

    for raw in reader:
        try:
            # Strip $ and parse odds
            odds_str = raw.get("Odds", "0").strip().replace("$", "")
            odds = float(odds_str) if odds_str else 0.0

            # Parse race number
            race_str = raw.get("Race", "0").strip()
            race = int(race_str) if race_str else 0

            # Parse stake
            stake_str = raw.get("Stake", "0").strip().replace("$", "")
            stake = float(stake_str) if stake_str else 0.0

            # Parse min_odds
            min_odds_str = raw.get("Min Odds", "0").strip().replace("$", "")
            min_odds = float(min_odds_str) if min_odds_str else 0.0

            # Detect row type: explicit 'Type' column, or infer from presence of event/bet_desc
            row_type = raw.get("Type", "").strip().lower()
            event = raw.get("Event", "").strip()
            bet_desc = raw.get("Bet", raw.get("Bet Desc", "")).strip()
            sport = raw.get("Sport", "").strip()

            if not row_type:
                # Auto-detect: if event or bet_desc present and no track, it's sports
                if (event or bet_desc) and not raw.get("Track", "").strip():
                    row_type = "sports"
                else:
                    row_type = "racing"

            rows.append(CsvBetRow(
                date=raw.get("Date", "").strip(),
                track=raw.get("Track", "").strip(),
                race=race,
                horse=raw.get("Horse", "").strip(),
                units=raw.get("Units", "").strip(),
                odds=odds,
                owner=raw.get("Owner", "").strip(),
                initials=raw.get("Initials", "").strip().upper(),
                bookmaker=raw.get("Bookmaker", "").strip(),
                stake_type=raw.get("Stake Type", "cash").strip().lower(),
                stake=stake,
                promotion=raw.get("Promotion", "").strip(),
                target_stake=raw.get("Target Stake", "").strip(),
                fully_allocated=raw.get("Fully Allocated", "").strip(),
                row_type=row_type,
                event=event,
                bet_desc=bet_desc,
                sport=sport,
                min_odds=min_odds,
            ))
        except Exception as e:
            logger.warning(f"Skipping malformed CSV row: {e} — raw: {raw}")
            continue

    return rows


async def validate_csv_rows(rows: list[CsvBetRow], username: str) -> list[dict]:
    """Validate each CSV row: check if bookmaker is supported, account exists.
    Returns a list of validation result dicts."""
    results = []

    for i, row in enumerate(rows):
        result = {
            "row_index": i,
            "row_type": row.row_type,
            "track": row.track,
            "race": row.race,
            "horse": row.horse,
            "event": row.event,
            "bet_desc": row.bet_desc,
            "sport": row.sport,
            "min_odds": row.min_odds,
            "initials": row.initials,
            "bookmaker": row.bookmaker,
            "stake": row.stake,
            "stake_type": row.stake_type,
            "odds": row.odds,
            "valid": False,
            "platform": None,
            "brand": None,
            "account_id": None,
            "error": None,
        }

        # Check bookmaker support
        bookie_key = normalize_bookmaker(row.bookmaker)
        mapping = get_platform_and_brand(row.bookmaker)
        if not mapping:
            if not is_supported(row.bookmaker):
                result["error"] = f"Unsupported bookmaker: {row.bookmaker}"
            else:
                result["error"] = f"Unknown bookmaker: {row.bookmaker}"
            results.append(result)
            continue

        platform, brand = mapping
        result["platform"] = platform
        result["brand"] = brand

        # Check account exists
        account = await find_account_by_initials_brand(username, row.initials, brand)
        if not account:
            result["error"] = f"No account for {row.initials} on {brand}"
            results.append(result)
            continue

        result["account_id"] = account["id"]
        result["valid"] = True
        results.append(result)

    return results


async def process_batch(batch_id: str, rows: list[dict], username: str):
    """Main batch orchestration: group rows, login, resolve races, place bets.

    rows: list of dicts with CSV data + validation info (platform, brand, account_id, row db id).
    """
    placed = 0
    failed = 0
    skipped = 0
    errors = []
    bet_count = 0

    # Group rows by (platform, brand, initials) for session reuse
    groups: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("status") == "skipped" or not row.get("account_id"):
            skipped += 1
            continue
        key = f"{row['platform']}:{row['brand']}:{row['initials']}"
        if key not in groups:
            groups[key] = []
        groups[key].append(row)

    for group_key, group_rows in groups.items():
        platform = group_rows[0]["platform"]
        brand = group_rows[0]["brand"]
        account_id = group_rows[0]["account_id"]

        # Build account dict for session manager
        account = await find_account_by_initials_brand(
            username, group_rows[0]["initials"], brand
        )
        if not account:
            for row in group_rows:
                await update_csv_row(row["id"], {
                    "status": "failed",
                    "error": f"Account not found for {group_rows[0]['initials']} on {brand}",
                })
                failed += 1
            continue

        # Get platform client
        try:
            client = get_client(platform)
        except ValueError as e:
            for row in group_rows:
                await update_csv_row(row["id"], {"status": "failed", "error": str(e)})
                failed += 1
            continue

        # Pre-fetch balance for this account group (check once, subtract as we place)
        cached_balance = None

        # Process each row in the group
        for row in group_rows:
            try:
                # Ensure session is valid (critical for Amused 300s tokens)
                session = await session_manager.ensure_valid(account)
                if not session.get("success"):
                    await update_csv_row(row["id"], {
                        "status": "failed",
                        "error": f"Login failed: {session.get('error', 'unknown')}",
                    })
                    failed += 1
                    continue

                # Balance pre-check (fetch once per account group, then subtract)
                bet_stake = float(row["stake"])
                if cached_balance is None:
                    try:
                        cached_balance = await client.get_balance(session)
                    except Exception as e:
                        logger.warning(f"Balance check failed for {group_key}: {e}")
                        cached_balance = float("inf")  # skip check if balance fetch fails

                if cached_balance < bet_stake:
                    await update_csv_row(row["id"], {
                        "status": "failed",
                        "error": f"Insufficient balance: ${cached_balance:.2f} available, ${bet_stake:.2f} required",
                    })
                    failed += 1
                    continue

                # Update row status to in_progress
                await update_csv_row(row["id"], {"status": "in_progress"})

                # Human delay: simulate browsing
                await asyncio.sleep(random.uniform(1.0, 2.5))

                # ── Sports bet routing ──
                # Detect sports rows: explicit row_type, or race=0 with bet desc in horse field
                is_sports = (row.get("row_type") == "sports"
                             or (str(row.get("race", "1")) == "0" and row.get("horse", ""))
                             )
                if is_sports:
                    # Sports fields: from in-memory rows or DB rows (track=event, horse=bet_desc)
                    event = row.get("event") or row.get("track", "")
                    bet_desc = row.get("bet_desc") or row.get("horse", "")
                    sport = row.get("sport", "")
                    target_odds = float(row.get("odds") or row.get("target_odds", 0) or 0)
                    min_odds = float(row.get("min_odds", 0) or 0)

                    result = await client.place_sports_bet(session, {
                        "event": event,
                        "bet_desc": bet_desc,
                        "sport": sport,
                        "odds": target_odds,
                        "min_odds": min_odds,
                        "stake": float(row["stake"]),
                        "stake_type": row.get("stake_type", "cash"),
                    })

                    if result.get("success"):
                        await update_csv_row(row["id"], {
                            "status": "placed",
                            "live_odds": str(result.get("odds", "")),
                            "bet_reference": result.get("bet_id", ""),
                            "raw_response": result,
                        })
                        placed += 1
                        if cached_balance != float("inf"):
                            cached_balance -= bet_stake
                        try:
                            await save_multi_bet({
                                "id": str(uuid.uuid4()),
                                "username": username,
                                "initials": row["initials"],
                                "owner_name": row.get("owner", ""),
                                "platform": row["platform"],
                                "brand": row["brand"],
                                "method": "allocation-sports",
                                "track": row.get("event", ""),
                                "race_number": 0,
                                "horse": row.get("bet_desc", ""),
                                "stake": float(row["stake"]),
                                "stake_type": row.get("stake_type", "cash"),
                                "odds": result.get("odds"),
                                "bet_reference": result.get("bet_id", ""),
                                "status": "placed",
                                "raw_response": result,
                            })
                        except Exception as e:
                            logger.warning(f"Failed to save sports multi_bet: {e}")
                    else:
                        await update_csv_row(row["id"], {
                            "status": "failed",
                            "error": result.get("error", "Sports placement failed"),
                            "raw_response": result,
                        })
                        failed += 1
                    continue

                # ── Racing bet routing ──

                # Find the race
                race_info = await client.find_race(session, row["track"], int(row["race"]))
                if not race_info:
                    await update_csv_row(row["id"], {
                        "status": "failed",
                        "error": f"Race not found: {row['track']} R{row['race']}",
                    })
                    failed += 1
                    continue

                # Get runners and find our horse
                runners = await client.get_runners(session, race_info)
                if not runners:
                    await update_csv_row(row["id"], {
                        "status": "failed",
                        "error": f"No runners found for {row['track']} R{row['race']}",
                    })
                    failed += 1
                    continue

                # Match horse by name (fuzzy)
                target_horse = row["horse"].lower().strip()
                matched_runner = None
                for runner in runners:
                    runner_name = runner.get("name", "").lower().strip()
                    if runner_name == target_horse or target_horse in runner_name:
                        matched_runner = runner
                        break

                if not matched_runner:
                    await update_csv_row(row["id"], {
                        "status": "failed",
                        "error": f"Horse not found: {row['horse']} in {row['track']} R{row['race']}",
                    })
                    failed += 1
                    continue

                live_odds = str(matched_runner.get("win_price", ""))

                # Human delay: simulate reviewing odds before betting
                await asyncio.sleep(random.uniform(1.5, 3.0))

                # Place the bet — brand_config is in the session (set by session_manager)
                result = await client.place_bet(
                    session=session,
                    race_info=race_info,
                    runner=matched_runner,
                    stake=float(row["stake"]),
                    stake_type=row.get("stake_type", "cash"),
                    brand_config=session.get("brand_config", {}),
                )

                if result.get("success"):
                    await update_csv_row(row["id"], {
                        "status": "placed",
                        "runner_id": str(matched_runner.get("id", "")),
                        "live_odds": live_odds,
                        "bet_reference": result.get("bet_id", ""),
                        "raw_response": result,
                    })
                    placed += 1

                    # Subtract stake from cached balance
                    if cached_balance != float("inf"):
                        cached_balance -= bet_stake

                    # Save to unified bet ledger
                    try:
                        await save_multi_bet({
                            "id": str(uuid.uuid4()),
                            "username": username,
                            "initials": row["initials"],
                            "owner_name": row.get("owner", ""),
                            "platform": row["platform"],
                            "brand": row["brand"],
                            "method": "allocation",
                            "track": row["track"],
                            "race_number": int(row["race"]),
                            "horse": row["horse"],
                            "stake": float(row["stake"]),
                            "stake_type": row.get("stake_type", "cash"),
                            "odds": float(live_odds) if live_odds else None,
                            "bet_reference": result.get("bet_id", ""),
                            "status": "placed",
                            "raw_response": result,
                        })
                    except Exception as e:
                        logger.warning(f"Failed to save multi_bet ledger entry: {e}")
                else:
                    await update_csv_row(row["id"], {
                        "status": "failed",
                        "runner_id": str(matched_runner.get("id", "")),
                        "live_odds": live_odds,
                        "error": result.get("error", "Unknown bet placement error"),
                        "raw_response": result,
                    })
                    failed += 1

            except Exception as e:
                logger.error(f"Error processing row {row.get('id')}: {e}", exc_info=True)
                await update_csv_row(row["id"], {
                    "status": "failed",
                    "error": f"Exception: {str(e)}",
                })
                failed += 1
                continue

            # Human-like delays between bets
            bet_count += 1
            if bet_count > 0 and bet_count % random.randint(15, 25) == 0:
                long_delay = random.uniform(60, 120)
                logger.info(f"Batch {batch_id}: long break {long_delay:.0f}s after {bet_count} bets")
                await asyncio.sleep(long_delay)
            else:
                delay = random.uniform(2, 6)
                logger.info(f"Batch {batch_id}: waiting {delay:.1f}s before next bet")
                await asyncio.sleep(delay)

    # Update batch summary
    summary = {
        "status": "completed",
        "placed": placed,
        "failed": failed,
        "skipped": skipped,
        "total": len(rows),
        "errors": errors[:50],  # cap error list
    }
    await update_batch_summary(batch_id, summary)
    logger.info(f"Batch {batch_id} completed: {placed} placed, {failed} failed, {skipped} skipped")

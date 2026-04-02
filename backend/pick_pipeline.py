"""
Auto-placement pipeline.
Telegram message → parse (text or vision) → filter bet365 → place via browser.
"""
import asyncio
import json
import logging
import os
import time
import uuid
from typing import Optional

from pick_parser import parse_message
from bet365_service import bet365_service, BET365_UNIT_SIZE

logger = logging.getLogger("pick_pipeline")

# ─── Pipeline State ──────────────────────────────────────────────────────────

class PickPipeline:
    """Orchestrates the full flow from Telegram message to bet placement."""

    def __init__(self):
        self._enabled = True
        self._pick_log: list[dict] = []  # All picks seen (placed or not)
        self._max_log = 500
        self._lock = asyncio.Lock()
        self._processing = False
        # Database save function — set by main.py after DB init
        self._save_pick_fn = None

    @property
    def status(self) -> dict:
        total = len(self._pick_log)
        placed = sum(1 for p in self._pick_log if p.get("placed"))
        failed = sum(1 for p in self._pick_log if p.get("attempted") and not p.get("placed"))
        skipped = sum(1 for p in self._pick_log if p.get("skipped"))
        return {
            "enabled": self._enabled,
            "processing": self._processing,
            "total_picks_seen": total,
            "placed": placed,
            "failed": failed,
            "skipped": skipped,
            "unit_size": BET365_UNIT_SIZE,
        }

    @property
    def pick_log(self) -> list[dict]:
        return list(self._pick_log)

    def enable(self):
        self._enabled = True
        logger.info("Pipeline ENABLED")

    def disable(self):
        self._enabled = False
        logger.info("Pipeline DISABLED")

    async def handle_telegram_message(self, msg: dict):
        """Called by TelegramMonitor when a new message arrives.

        msg keys: text, image_bytes, mime_type, timestamp, message_id
        """
        if not self._enabled:
            logger.info("Pipeline disabled, ignoring message")
            return

        async with self._lock:
            self._processing = True
            try:
                await self._process_message(msg)
            except Exception as e:
                logger.error(f"Pipeline error: {e}")
            finally:
                self._processing = False

    async def _process_message(self, msg: dict):
        """Parse message and place qualifying picks."""
        text = msg.get("text")
        image_bytes = msg.get("image_bytes")
        mime_type = msg.get("mime_type", "image/jpeg")
        timestamp = msg.get("timestamp", time.time())
        message_id = msg.get("message_id")

        # Step 1: Parse all picks from message
        all_picks = await parse_message(
            text=text,
            image_bytes=image_bytes,
            mime_type=mime_type,
        )

        if not all_picks:
            logger.info("No picks found in message")
            # Still log the message
            self._log_pick({
                "message_id": message_id,
                "timestamp": timestamp,
                "source": "screenshot" if image_bytes else "text",
                "raw": text[:200] if text else "[image only]",
                "parsed": False,
                "picks_found": 0,
                "skipped": True,
                "skip_reason": "no_picks_found",
            })
            return

        logger.info(f"Found {len(all_picks)} picks in message")

        # Step 2: Filter for bet365 picks only
        for pick in all_picks:
            bookie = (pick.get("bookie") or "").lower().strip()
            is_bet365 = bookie in ("bet365", "b365", "365")

            pick_entry = {
                "id": str(uuid.uuid4()),
                "message_id": message_id,
                "timestamp": timestamp,
                "source": pick.get("source", "text"),
                "raw": pick.get("raw", ""),
                "parsed": True,
                "bookie": pick.get("bookie", ""),
                "sport": pick.get("sport", ""),
                "player": pick.get("player", ""),
                "stat": pick.get("stat", ""),
                "line": pick.get("line", 0),
                "side": pick.get("side", ""),
                "odds": pick.get("odds", 0),
                "units": pick.get("units", 1),
            }

            if not is_bet365:
                pick_entry["skipped"] = True
                pick_entry["skip_reason"] = f"wrong_bookie:{bookie}"
                self._log_pick(pick_entry)
                logger.info(f"Skipping {pick['player']} — bookie is '{bookie}', not bet365")
                continue

            # Validate required fields
            if not pick.get("player") or not pick.get("stat") or not pick.get("line"):
                pick_entry["skipped"] = True
                pick_entry["skip_reason"] = "missing_fields"
                self._log_pick(pick_entry)
                logger.warning(f"Skipping pick with missing fields: {pick}")
                continue

            if not pick.get("side"):
                pick_entry["skipped"] = True
                pick_entry["skip_reason"] = "missing_side"
                self._log_pick(pick_entry)
                logger.warning(f"Skipping pick with no side (over/under): {pick}")
                continue

            # Step 3: Check if bet365 browser is alive
            if not bet365_service.is_alive:
                logger.warning("bet365 browser not alive, attempting start...")
                started = await bet365_service.start()
                if not started:
                    pick_entry["attempted"] = True
                    pick_entry["placed"] = False
                    pick_entry["error"] = "bet365 browser not running"
                    self._log_pick(pick_entry)
                    logger.error("Cannot place bet — bet365 browser failed to start")
                    continue

            # Step 4: Place the bet
            logger.info(f"PLACING: {pick['player']} {pick['side']} {pick['line']} {pick['stat']} @ {pick['odds']}")

            result = await bet365_service.place_pick(pick)

            pick_entry["attempted"] = True
            pick_entry["placed"] = result.get("success", False)
            pick_entry["actual_odds"] = result.get("actual_odds", "")
            pick_entry["stake"] = result.get("stake", 0)
            pick_entry["potential_return"] = result.get("potential_return", "")
            pick_entry["error"] = result.get("error", "")
            pick_entry["screenshot"] = result.get("screenshot", "")

            self._log_pick(pick_entry)

            if result.get("success"):
                logger.info(f"PLACED: {pick['player']} {pick['side']} {pick['line']} {pick['stat']} — odds={result.get('actual_odds')}")
            else:
                logger.error(f"FAILED: {pick['player']} — {result.get('error')}")

            # Save to DB if function is set
            if self._save_pick_fn:
                try:
                    await self._save_pick_fn(pick_entry)
                except Exception as e:
                    logger.error(f"Failed to save pick to DB: {e}")

            # Small delay between placements
            await asyncio.sleep(2)

    def _log_pick(self, entry: dict):
        """Add to in-memory log."""
        self._pick_log.append(entry)
        if len(self._pick_log) > self._max_log:
            self._pick_log = self._pick_log[-self._max_log:]


# Singleton
pick_pipeline = PickPipeline()

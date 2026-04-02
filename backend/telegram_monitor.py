"""
Telegram channel monitor using Telethon.
Watches a relay channel for new picks (text and screenshots).
Feeds them into the pick pipeline for parsing and auto-placement.
"""
import asyncio
import io
import logging
import os
import time
from typing import Optional, Callable, Awaitable

logger = logging.getLogger("telegram_monitor")

TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TELEGRAM_CHANNEL_ID = int(os.getenv("TELEGRAM_CHANNEL_ID", "0"))
TELEGRAM_SESSION_PATH = os.path.join(os.path.dirname(__file__), "telegram_session")


class TelegramMonitor:
    """Watches a Telegram channel and forwards messages to a callback."""

    def __init__(self):
        self._client = None
        self._running = False
        self._callback: Optional[Callable] = None
        self._message_log: list[dict] = []  # Recent messages for dashboard
        self._max_log = 100

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def status(self) -> dict:
        return {
            "running": self._running,
            "channel_id": TELEGRAM_CHANNEL_ID,
            "recent_messages": len(self._message_log),
        }

    @property
    def recent_messages(self) -> list[dict]:
        return list(self._message_log)

    def set_callback(self, callback: Callable[[dict], Awaitable[None]]):
        """Set the async callback for new messages.
        callback receives: {text, image_bytes, mime_type, timestamp, message_id}
        """
        self._callback = callback

    async def start(self):
        """Start monitoring the channel."""
        if self._running:
            logger.info("Monitor already running")
            return

        if not TELEGRAM_API_ID or not TELEGRAM_API_HASH or not TELEGRAM_CHANNEL_ID:
            logger.error("Telegram credentials not configured")
            return

        try:
            from telethon import TelegramClient, events

            self._client = TelegramClient(
                TELEGRAM_SESSION_PATH,
                TELEGRAM_API_ID,
                TELEGRAM_API_HASH,
            )

            await self._client.start()
            logger.info("Telegram client connected")

            # Resolve channel entity
            try:
                channel = await self._client.get_entity(TELEGRAM_CHANNEL_ID)
                logger.info(f"Monitoring channel: {getattr(channel, 'title', TELEGRAM_CHANNEL_ID)}")
            except Exception as e:
                logger.error(f"Could not resolve channel {TELEGRAM_CHANNEL_ID}: {e}")
                return

            @self._client.on(events.NewMessage(chats=TELEGRAM_CHANNEL_ID))
            async def handler(event):
                await self._handle_message(event)

            self._running = True
            logger.info("Telegram monitor started")

        except Exception as e:
            logger.error(f"Failed to start Telegram monitor: {e}")
            self._running = False

    async def stop(self):
        """Stop monitoring."""
        self._running = False
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
        logger.info("Telegram monitor stopped")

    async def run_forever(self):
        """Run the Telethon event loop. Call this as a background task."""
        if not self._client:
            await self.start()
        if self._client:
            try:
                await self._client.run_until_disconnected()
            except Exception as e:
                logger.error(f"Telegram monitor disconnected: {e}")
            finally:
                self._running = False

    async def _handle_message(self, event):
        """Process an incoming message from the channel."""
        msg = event.message
        timestamp = time.time()

        text = msg.text or msg.message or ""
        image_bytes = None
        mime_type = "image/jpeg"

        # Check for photo/image
        if msg.photo or (msg.document and msg.document.mime_type and msg.document.mime_type.startswith("image/")):
            try:
                buffer = io.BytesIO()
                await self._client.download_media(msg, file=buffer)
                image_bytes = buffer.getvalue()
                if msg.document and msg.document.mime_type:
                    mime_type = msg.document.mime_type
                logger.info(f"Downloaded image: {len(image_bytes)} bytes")
            except Exception as e:
                logger.error(f"Failed to download image: {e}")

        # Log the message
        log_entry = {
            "message_id": msg.id,
            "timestamp": timestamp,
            "has_text": bool(text),
            "has_image": image_bytes is not None,
            "text_preview": text[:200] if text else None,
            "image_size": len(image_bytes) if image_bytes else 0,
        }
        self._message_log.append(log_entry)
        if len(self._message_log) > self._max_log:
            self._message_log = self._message_log[-self._max_log:]

        logger.info(f"New message: text={bool(text)}, image={image_bytes is not None}")

        # Forward to callback
        if self._callback:
            try:
                await self._callback({
                    "text": text,
                    "image_bytes": image_bytes,
                    "mime_type": mime_type,
                    "timestamp": timestamp,
                    "message_id": msg.id,
                })
            except Exception as e:
                logger.error(f"Callback error: {e}")


# Singleton
telegram_monitor = TelegramMonitor()

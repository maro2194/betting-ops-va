"""
Unified pick parser — handles both text messages and screenshot images.
Text: regex-based parser (proven from exploration).
Images: Claude Vision API identifies bookie, player, stat, line, side, odds.
"""
import base64
import logging
import os
import re
from typing import Optional

logger = logging.getLogger("pick_parser")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ─── Text Parser ─────────────────────────────────────────────────────────────

SPORT_EMOJI_MAP = {
    "⚾": "MLB", "🏀": "NBA", "🏈": "NFL", "⚽": "Soccer",
    "🏉": "NRL", "🏏": "Cricket", "🐎": "Horse Racing",
}


def parse_text_picks(text: str) -> list[dict]:
    """Parse Telegram text picks into structured dicts.

    Supports formats like:
        ⚾ MLB — Austin Martin U1.5 Total Bases
          💰 $1.87 | 2u | Bet365

        🏀 NBA — Jalen Green U4.5 Rebounds
          💰 $2.22 | 2u | Bet365
    """
    bets = []
    blocks = re.split(r'\n\n+|(?=[\u2600-\u27BF])', text.strip())

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        lines = block.split("\n")
        if len(lines) < 2:
            continue

        pick_line = lines[0].strip()
        info_line = lines[1].strip() if len(lines) > 1 else ""

        # Format: ⚾ MLB — Player O/U Line Stat
        pick_match = re.match(
            r'([^\w]*)\s*(\w+)\s*[—–\-]\s*(.+?)\s+(O|U|Over|Under)\s*(\d+\.?\d*)\s+(.+)',
            pick_line, re.IGNORECASE,
        )
        if not pick_match:
            continue

        sport = pick_match.group(2).strip()
        player = pick_match.group(3).strip()
        side_raw = pick_match.group(4).strip()
        line = pick_match.group(5).strip()
        stat = pick_match.group(6).strip()

        side = "Over" if side_raw in ("O", "Over") else "Under"

        # Format: 💰 $1.87 | 2u | Bet365
        info_match = re.search(r'\$(\d+\.?\d*)\s*\|\s*([\d.]+)u\s*\|\s*(\w[\w\d]*)', info_line)
        odds = float(info_match.group(1)) if info_match else 0
        units = float(info_match.group(2)) if info_match else 1
        bookie = info_match.group(3) if info_match else ""

        bets.append({
            "sport": sport.upper(),
            "player": player,
            "stat": stat.lower(),
            "line": float(line),
            "side": side,
            "odds": odds,
            "units": units,
            "bookie": bookie,
            "source": "text",
            "raw": pick_line,
        })

    return bets


# ─── Vision Parser (Claude API) ─────────────────────────────────────────────

VISION_PROMPT = """You are analyzing a screenshot of a sports betting pick or betslip.

Extract ALL betting picks visible in the image. For each pick, identify:

1. **bookie** — Which bookmaker/app is this from? (e.g. "Bet365", "Sportsbet", "TAB", "Ladbrokes", "PointsBet", "BetDeluxe", etc.). Look at the UI design, logo, colors, branding to determine this.
2. **sport** — The sport (e.g. "NBA", "MLB", "AFL", "NRL", "Soccer", "Tennis")
3. **player** — The player name
4. **stat** — The statistical category (e.g. "rebounds", "points", "assists", "disposals", "hits", "total bases", "3 pointers")
5. **line** — The line/threshold number (e.g. 4.5, 12.5, 1.5)
6. **side** — "Over" or "Under"
7. **odds** — The decimal odds (e.g. 2.22, 1.87)
8. **units** — The units/stake if visible, otherwise default to 0

If the image is NOT a betting pick (e.g. a meme, chat message, random image), return an empty array.

Return your response as a JSON array only, no other text. Example:
[
  {"bookie": "Bet365", "sport": "NBA", "player": "Jalen Green", "stat": "rebounds", "line": 4.5, "side": "Under", "odds": 2.22, "units": 2},
  {"bookie": "Bet365", "sport": "NBA", "player": "Victor Wembanyama", "stat": "rebounds", "line": 12.5, "side": "Over", "odds": 2.30, "units": 1.5}
]

If you cannot determine a field, use null for that field. Return ONLY the JSON array."""


async def parse_image_picks(image_bytes: bytes, mime_type: str = "image/jpeg") -> list[dict]:
    """Parse a screenshot using Claude Vision API.

    Returns list of structured pick dicts, same format as parse_text_picks.
    """
    if not ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY not set, cannot parse images")
        return []

    try:
        import httpx

        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        payload = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 2000,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": b64_image,
                        },
                    },
                    {
                        "type": "text",
                        "text": VISION_PROMPT,
                    },
                ],
            }],
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                json=payload,
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )

        if resp.status_code != 200:
            logger.error(f"Claude API error: {resp.status_code} {resp.text[:300]}")
            return []

        data = resp.json()
        text_content = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_content += block["text"]

        # Extract JSON array from response
        json_match = re.search(r'\[.*\]', text_content, re.DOTALL)
        if not json_match:
            logger.warning(f"No JSON array in Vision response: {text_content[:200]}")
            return []

        import json
        picks_raw = json.loads(json_match.group())

        # Normalize to our standard format
        picks = []
        for p in picks_raw:
            if not isinstance(p, dict):
                continue
            picks.append({
                "sport": (p.get("sport") or "").upper(),
                "player": p.get("player") or "",
                "stat": (p.get("stat") or "").lower(),
                "line": float(p["line"]) if p.get("line") is not None else 0,
                "side": p.get("side") or "",
                "odds": float(p["odds"]) if p.get("odds") is not None else 0,
                "units": float(p["units"]) if p.get("units") is not None else 0,
                "bookie": p.get("bookie") or "",
                "source": "screenshot",
                "raw": f"[screenshot] {p.get('player', '')} {p.get('side', '')} {p.get('line', '')} {p.get('stat', '')}",
            })

        logger.info(f"Vision parsed {len(picks)} picks from screenshot")
        return picks

    except Exception as e:
        logger.error(f"Vision parse error: {e}")
        return []


# ─── Unified Parser ──────────────────────────────────────────────────────────

async def parse_message(text: Optional[str] = None, image_bytes: Optional[bytes] = None,
                        mime_type: str = "image/jpeg") -> list[dict]:
    """Parse a Telegram message (text, image, or both) into structured picks.

    Returns all picks found, regardless of bookie. Caller filters for bet365.
    """
    picks = []

    if text and text.strip():
        text_picks = parse_text_picks(text)
        picks.extend(text_picks)
        if text_picks:
            logger.info(f"Parsed {len(text_picks)} picks from text")

    if image_bytes:
        image_picks = await parse_image_picks(image_bytes, mime_type)
        picks.extend(image_picks)
        if image_picks:
            logger.info(f"Parsed {len(image_picks)} picks from image")

    return picks

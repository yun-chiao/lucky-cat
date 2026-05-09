from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from pydantic import BaseModel
from web3 import Web3

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=BASE_DIR / ".env", override=True)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("lucky-cat-backend")

# -----------------------------------------------------------------------------
# Basic mode: required runtime config
# These variables are enough for the backend to poll GeckoTerminal, infer a
# state, keep an in-memory cache, and serve the frontend API.
# -----------------------------------------------------------------------------
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "45"))
REFRESH_CRON_MINUTES = os.getenv("REFRESH_CRON_MINUTES", "1,16,31,46")
GECKO_NETWORK = os.getenv("GECKO_NETWORK", "mantle")
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
OHLCV_AGGREGATE_MINUTES = int(os.getenv("OHLCV_AGGREGATE_MINUTES", "15"))
OHLCV_RETRY_DELAYS_SECONDS = [20, 40]

# -----------------------------------------------------------------------------
# Phase 2 mode: optional AI + on-chain write config
# If these values are missing, the backend should still work in Basic mode.
# -----------------------------------------------------------------------------
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini").lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

MANTLE_RPC_URL = os.getenv("MANTLE_RPC_URL", "")
CHAIN_ID = int(os.getenv("CHAIN_ID", "5000"))
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS", "")

# Basic mode: token watchlist configuration
# WATCH_TOKENS format: "mETH:0xabc..."
WATCH_TOKENS_RAW = os.getenv("WATCH_TOKENS", "")
WATCH_TOKENS: dict[str, str] = {}
for item in WATCH_TOKENS_RAW.split(","):
    item = item.strip()
    if not item or ":" not in item:
        continue
    symbol, address = item.split(":", 1)
    symbol = symbol.strip()
    address = address.strip()
    if symbol and address:
        WATCH_TOKENS[symbol] = address

if not WATCH_TOKENS:
    # Fallback for local demo when token addresses are not configured yet.
    WATCH_TOKENS = {
        "mETH": "0x0000000000000000000000000000000000000000",
    }

# -----------------------------------------------------------------------------
# Phase 2 mode clients
# These clients are optional. If credentials are absent, the backend continues
# to run in Basic mode with cache updates and fallback text.
# -----------------------------------------------------------------------------
ai_client = None
AI_MODEL = ""

if AI_PROVIDER == "gemini":
    gemini_key = GEMINI_API_KEY or OPENAI_API_KEY
    if gemini_key:
        ai_client = OpenAI(
            api_key=gemini_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        AI_MODEL = GEMINI_MODEL
elif AI_PROVIDER == "openai" and OPENAI_API_KEY:
    ai_client = OpenAI(api_key=OPENAI_API_KEY)
    AI_MODEL = OPENAI_MODEL

web3 = Web3(Web3.HTTPProvider(MANTLE_RPC_URL)) if MANTLE_RPC_URL else None
web3_account = web3.eth.account.from_key(PRIVATE_KEY) if web3 and PRIVATE_KEY else None

LUCKY_CAT_STATE_ABI = [
    {
        "inputs": [
            {"internalType": "string", "name": "_state", "type": "string"},
            {"internalType": "string", "name": "_message", "type": "string"},
        ],
        "name": "updateState",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

contract = (
    web3.eth.contract(address=Web3.to_checksum_address(CONTRACT_ADDRESS), abi=LUCKY_CAT_STATE_ABI)
    if web3 and CONTRACT_ADDRESS
    else None
)

CHAIN_WRITE_ENABLED = bool(web3 and web3_account and contract)

logger.info(
    "Backend boot: ai_provider=%s ai_enabled=%s chain_write_enabled=%s",
    AI_PROVIDER,
    bool(ai_client),
    CHAIN_WRITE_ENABLED,
)


def _extract_ai_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            else:
                item_type = getattr(item, "type", None)
                text = getattr(item, "text", None)
                if item_type == "text" and isinstance(text, str):
                    parts.append(text)
        return " ".join(parts).strip()
    return ""


def _parse_ai_json_response(text: str) -> dict[str, Any] | None:
    """Best-effort parse for model outputs that may wrap JSON in prose/fences."""
    raw = (text or "").strip()
    if not raw:
        return None

    # Common case: fenced code block, optionally with a json language tag.
    fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", raw, flags=re.IGNORECASE)
    candidate = fence_match.group(1).strip() if fence_match else raw

    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    # Recovery: extract from first '{' to last '}' when model adds extra text.
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        parsed = json.loads(candidate[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _is_low_quality_ai_text(text: str) -> bool:
    cleaned = (text or "").strip()
    if len(cleaned) < 12:
        return True
    if cleaned.endswith((",", ":", "|", ";")):
        return True
    if re.search(r"\bstate\b", cleaned, flags=re.IGNORECASE):
        return True
    if re.search(r"\b(i|we|you|he|she|they|it|to|for|with|and|or|but|so)$", cleaned, flags=re.IGNORECASE):
        return True
    words = [w for w in re.split(r"\s+", cleaned) if w]
    return len(words) < 3


def _normalize_for_similarity(text: str) -> str:
    lowered = (text or "").lower().strip()
    lowered = re.sub(r"\$?\d+(?:,\d{3})*(?:\.\d+)?", "<num>", lowered)
    lowered = re.sub(r"[^a-z0-9%<>\s]", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered


def _is_repetitive_message(candidate: str, previous_messages: list[str]) -> bool:
    norm = _normalize_for_similarity(candidate)
    if not norm:
        return True
    for prev in previous_messages:
        prev_norm = _normalize_for_similarity(prev)
        if not prev_norm:
            continue
        if norm == prev_norm:
            return True
        if len(norm) > 18 and (norm in prev_norm or prev_norm in norm):
            return True
    return False


def _cat_style_hint(state: str, history_len: int) -> str:
    # Rotate style cues to force visible variation between close requests.
    cues = {
        "idle": [
            "curious observer cat tone",
            "playful paw-tap watcher tone",
            "sleepy-but-alert cat tone",
            "mischievous tail-swish tone",
        ],
        "alpha": [
            "proud hunter cat tone",
            "zoomies victory tone",
            "confident rooftop king cat tone",
            "teasing tsundere cat tone",
        ],
        "risk": [
            "hissing warning cat tone",
            "arched-back danger alert tone",
            "sharp-claw protective tone",
            "low-growl caution tone",
        ],
    }
    options = cues.get(state, cues["idle"])
    idx = (history_len + datetime.now(timezone.utc).microsecond) % len(options)
    return options[idx]


def _fallback_message(state: str, token: str) -> str:
    fallback = {
        "idle": f"I'm watching {token} for you...",
        "alpha": f"{token} is picking up. Try not to be late this time.",
        "risk": f"{token} risk is rising. Stay sharp.",
    }
    return fallback[state]


def _state_label(state: str) -> str:
    mapping = {
        "idle": "Watching",
        "alpha": "Bullish",
        "risk": "Risk",
    }
    return mapping.get(state, state)


def _compact_metric(metric: str) -> str:
    # Keep the first 2 most meaningful parts for a concise message.
    parts = [p.strip() for p in (metric or "").split("|") if p.strip()]
    if not parts:
        return ""
    return " | ".join(parts[:2])


def _short_message(state: str, token: str, metric: str) -> str:
    price_hint = _compact_metric(metric)
    variants = {
        "idle": [
            f"Whiskers twitching, I am quietly watching {token}.",
            f"Soft paws on the roof, {token} is calm for now.",
            f"Tail curled and eyes open, {token} feels quiet.",
        ],
        "alpha": [
            f"The mouse is getting lively around {token}.",
            f"Paws are quick tonight, {token} is waking up.",
            f"I can hear excited steps around {token}.",
        ],
        "risk": [
            f"The wind is picking up around {token}, stay alert.",
            f"Fur standing up, {token} feels tense right now.",
            f"Low growl mode: {token} is getting jumpy.",
        ],
    }
    options = variants.get(state, variants["idle"])
    idx = (datetime.now(timezone.utc).microsecond + len(token)) % len(options)
    line = options[idx]
    if price_hint and state != "idle":
        return f"{line} ({price_hint})"
    return line


def _should_retry_ai_message(state: str, token: str, metric: str, current_message: str) -> bool:
    if not current_message:
        return True

    # Startup default copy from WIDGET_CACHE.
    if current_message == "哼，先幫你盯著鏈上風吹草動。":
        return True

    # Fallback copy generated by generate_cat_message.
    if current_message == _fallback_message(state, token):
        return True

    # Keep idle in a fixed tsundere watcher tone.
    if state == "idle":
        return False

    # If message does not reflect latest metric snapshot, refresh it.
    metric_head = metric.split("|")[0].strip() if metric else ""
    if metric_head and metric_head not in current_message:
        return True

    return False

# Shared runtime services for both modes
http_client = httpx.AsyncClient(timeout=15)
scheduler = AsyncIOScheduler(timezone="UTC")


class WidgetPayload(BaseModel):
    state: str
    message: str
    metric: str
    token: str
    updatedAt: str


class TokenSourceStatus(BaseModel):
    symbol: str
    address: str
    ok: bool
    httpStatus: int | None
    volM5: float
    buysM5: int
    sellsM5: int
    priceChange24h: float
    lastFetchAt: str | None
    error: str | None


class DebugSourcePayload(BaseModel):
    geckoNetwork: str
    pollSeconds: int
    updatedAt: str
    sources: list[TokenSourceStatus]


class ChainWriteStatus(BaseModel):
    enabled: bool
    chainId: int
    contractAddress: str
    walletAddress: str
    lastAttemptAt: str | None
    lastState: str | None
    lastMessage: str | None
    lastTxHash: str | None
    lastSuccess: bool
    lastError: str | None


class DemoWriteRequest(BaseModel):
    state: str | None = None
    priceUsd: float | None = None
    message: str | None = None


class UserRulesRequest(BaseModel):
    rules: str


class HistoryEntry(BaseModel):
    timestamp: str
    state: str
    message: str
    metric: str
    token: str
    priceUsd: float
    priceChange24h: float
    buysM5: int
    sellsM5: int
    volM5: float
    ruleApplied: bool
    source: str
    txHash: str | None = None
    chainId: int | None = None


# Basic mode: in-memory widget cache returned to the frontend
WIDGET_CACHE = WidgetPayload(
    state="idle",
    message="哼，先幫你盯著鏈上風吹草動。",
    metric="N/A",
    token="mETH",
    updatedAt=datetime.now(timezone.utc).isoformat(),
)

# Basic mode: short history for local state inference
VOLUME_HISTORY: dict[str, deque[float]] = {symbol: deque(maxlen=12) for symbol in WATCH_TOKENS}

# Basic mode: debug cache for inspecting upstream GeckoTerminal results
TOKEN_SOURCE_CACHE: dict[str, TokenSourceStatus] = {
    symbol: TokenSourceStatus(
        symbol=symbol,
        address=address,
        ok=False,
        httpStatus=None,
        volM5=0.0,
        buysM5=0,
        sellsM5=0,
        priceChange24h=0.0,
        lastFetchAt=None,
        error="not-fetched-yet",
    )
    for symbol, address in WATCH_TOKENS.items()
}

LAST_CHAIN_WRITE = ChainWriteStatus(
    enabled=CHAIN_WRITE_ENABLED,
    chainId=CHAIN_ID,
    contractAddress=CONTRACT_ADDRESS,
    walletAddress=web3_account.address if web3_account else "",
    lastAttemptAt=None,
    lastState=None,
    lastMessage=None,
    lastTxHash=None,
    lastSuccess=False,
    lastError=None,
)

# Rule engine: user-defined rules (in-memory, reset on restart)
USER_RULES: str = ""

# Decision history: ring buffer of last 50 evaluations (most-recent first)
DECISION_HISTORY: deque[dict] = deque(maxlen=50)

# Preset fake candle snapshots for demo mode — realistic but static
DEMO_SNAP_PRESETS: dict[str, dict] = {
    "idle": {
        "symbol": "mETH",
        "ok": True,
        "price_usd": 2300.0,
        "price_change_24h": -0.8,
        "vol_m5": 8500.0,
        "buys_m5": 4,
        "sells_m5": 3,
    },
    "alpha": {
        "symbol": "mETH",
        "ok": True,
        "price_usd": 4100.0,
        "price_change_24h": 6.3,
        "vol_m5": 45000.0,
        "buys_m5": 12,
        "sells_m5": 2,
    },
    "risk": {
        "symbol": "mETH",
        "ok": True,
        "price_usd": 1500.0,
        "price_change_24h": -7.1,
        "vol_m5": 38000.0,
        "buys_m5": 2,
        "sells_m5": 14,
    },
}

RULE_ENGINE_SYSTEM_PROMPT = """
You are Lucky Cat, a strict rule-execution DeFi signal analyst.

Priority policy (critical):
1) User rules are the highest priority and must be followed exactly.
2) Do NOT override user rules with momentum, volume, or buy/sell pressure.
3) Use market features only to explain the result, not to change the rule result.

Rule execution policy:
- Evaluate rules in order, top to bottom.
- If a rule matches, use its mapped state.
- If no earlier rule matches and an "otherwise" rule exists, use that state.

Example:
- Rules: price > 4200 => alpha; price < 2000 => risk; otherwise => idle
- price=4100 must return idle (not alpha)

Output format:
Return ONLY valid JSON (no markdown, no extra text):
{
    "state": "idle|alpha|risk",
    "message": "1 concise but overall line, the tone needs to like a cat, avoid sounding like a financial analyst. Do not mention the rules or the state in the message, just describe the market in a cute cat way. For example, 'The mouse is getting lively!' or 'The wind is picking up, stay alert!'.",
    "summary": "short rationale including which rule matched",
    "confidence": 0-100,
    "keySignals": ["signal 1", "signal 2", "signal 3"],
    "appliedRule": "the exact matching rule text or 'otherwise'"
}

Message requirements:
- Keep under 180 characters
- Avoid generic text; be specific and useful
""".strip()

DEFAULT_DEMO_RULES = """
price >= 4000 => alpha
price <= 2000 => risk
otherwise => idle
""".strip()


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _pick_largest_pool(pools: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not pools:
        return None

    def reserve(pool: dict[str, Any]) -> float:
        attrs = pool.get("attributes") or {}
        return _safe_float(attrs.get("reserve_in_usd"))

    return max(pools, key=reserve)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Phase 2 mode: optional on-chain write helper
# Safe to call even when chain config is missing; it will no-op and return None.
def write_state_to_chain(state: str, message: str) -> str | None:
    if not CHAIN_WRITE_ENABLED or not web3 or not web3_account or not contract:
        return None

    try:
        nonce = web3.eth.get_transaction_count(web3_account.address, "pending")
        gas_price = web3.eth.gas_price
        tx = contract.functions.updateState(state, message).build_transaction(
            {
                "from": web3_account.address,
                "nonce": nonce,
                "chainId": CHAIN_ID,
                "gasPrice": gas_price,
            }
        )

        if "gas" not in tx:
            try:
                tx["gas"] = contract.functions.updateState(state, message).estimate_gas(
                    {"from": web3_account.address}
                )
            except Exception:
                tx["gas"] = 250000

        signed = web3_account.sign_transaction(tx)
        raw_tx = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
        tx_hash = web3.eth.send_raw_transaction(raw_tx)
        return tx_hash.hex()
    except Exception as exc:
        logger.exception("write_state_to_chain failed: %s", exc)
        return None


async def fetch_token_snapshot(symbol: str, address: str) -> dict[str, Any]:
    # Basic mode: fetch current token pool summary from GeckoTerminal.
    url = f"https://api.geckoterminal.com/api/v2/networks/{GECKO_NETWORK}/tokens/{address}/pools"
    response = await http_client.get(url, params={"page": 1})
    if response.status_code >= 400:
        return {
            "symbol": symbol,
            "ok": False,
            "http_status": response.status_code,
            "vol_m5": 0.0,
            "buys_m5": 0,
            "sells_m5": 0,
            "price_change_24h": 0.0,
            "error": f"upstream-{response.status_code}",
        }

    data = response.json().get("data") or []
    if not data:
        return {
            "symbol": symbol,
            "ok": True,
            "http_status": response.status_code,
            "vol_m5": 0.0,
            "buys_m5": 0,
            "sells_m5": 0,
            "price_change_24h": 0.0,
            "error": "empty-data",
        }

    largest_pool = _pick_largest_pool(data)
    if not largest_pool:
        return {
            "symbol": symbol,
            "ok": True,
            "http_status": response.status_code,
            "vol_m5": 0.0,
            "buys_m5": 0,
            "sells_m5": 0,
            "price_change_24h": 0.0,
            "error": "empty-data",
        }

    attrs = largest_pool.get("attributes", {})
    pool_address = (attrs.get("address") or "").strip().lower()
    volume_usd = attrs.get("volume_usd", {})
    txns = attrs.get("transactions", {}).get("m5", {})
    price_change = attrs.get("price_change_percentage", {})

    price_usd = _safe_float(attrs.get("base_token_price_usd"))
    candle_open_time = None
    if pool_address:
        ohlcv_url = f"https://api.geckoterminal.com/api/v2/networks/{GECKO_NETWORK}/pools/{pool_address}/ohlcv/minute"
        for attempt in range(len(OHLCV_RETRY_DELAYS_SECONDS) + 1):
            try:
                ohlcv_resp = await http_client.get(
                    ohlcv_url,
                    params={"aggregate": OHLCV_AGGREGATE_MINUTES, "limit": 1},
                )
                if ohlcv_resp.status_code < 400:
                    ohlcv = ((ohlcv_resp.json().get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
                    if ohlcv and isinstance(ohlcv[0], list) and len(ohlcv[0]) >= 6:
                        # [timestamp, open, high, low, close, volume]
                        candle_open_time = int(ohlcv[0][0])
                        close_price = _safe_float(ohlcv[0][4])
                        if close_price > 0:
                            price_usd = close_price
                            break
            except Exception:
                pass

            if attempt < len(OHLCV_RETRY_DELAYS_SECONDS):
                await asyncio.sleep(OHLCV_RETRY_DELAYS_SECONDS[attempt])

    return {
        "symbol": symbol,
        "ok": True,
        "http_status": response.status_code,
        "vol_m5": _safe_float(volume_usd.get("m5")),
        "buys_m5": int(txns.get("buys") or 0),
        "sells_m5": int(txns.get("sells") or 0),
        "price_change_24h": _safe_float(price_change.get("h24")),
        "price_usd": price_usd,
        "candle_open_time": candle_open_time,
        "error": None,
    }


def evaluate_meth_state(data: dict[str, Any]) -> str:
    price_change_24h = _safe_float(data.get("price_change_24h"))
    vol_m5 = _safe_float(data.get("vol_m5"))
    buys_m5 = int(data.get("buys_m5") or 0)
    sells_m5 = int(data.get("sells_m5") or 0)

    if price_change_24h < -5.0 or (sells_m5 > buys_m5 * 3 and vol_m5 > 10000):
        return "risk"

    if price_change_24h > 5.0 or (buys_m5 > sells_m5 * 3 and vol_m5 > 10000):
        return "alpha"

    return "idle"


def build_meth_metric(data: dict[str, Any], state: str) -> str:
    price_usd = _safe_float(data.get("price_usd"))
    if price_usd <= 0:
        return "N/A"
    return f"${price_usd:,.1f}"


def build_price_metric(data: dict[str, Any]) -> str:
    price_usd = _safe_float(data.get("price_usd"))
    if price_usd <= 0:
        return "N/A"
    return f"${price_usd:,.1f}"


def build_rich_metric(data: dict[str, Any]) -> str:
    price_usd = _safe_float(data.get("price_usd"))
    price_change_24h = _safe_float(data.get("price_change_24h"))
    buys_m5 = int(data.get("buys_m5") or 0)
    sells_m5 = int(data.get("sells_m5") or 0)
    vol_m5 = _safe_float(data.get("vol_m5"))

    if price_usd <= 0:
        return "N/A"

    return (
        f"${price_usd:,.1f}"
        f" | 24h {price_change_24h:+.2f}%"
        f" | B/S {buys_m5}/{sells_m5}"
        f" | Vol ${vol_m5:,.0f}"
    )


def _evaluate_price_threshold_rules(rules: str, price_usd: float) -> str | None:
    """Deterministically evaluate simple rules like:
    - price > 4200 => alpha
    - price < 2000 => risk
    - otherwise => idle
    Returns state or None when rule format is not recognized.
    """
    text = (rules or "").lower()
    if not text:
        return None

    upper_ge_match = re.search(r"price\s*>\s*=\s*(\d+(?:\.\d+)?)\s*(?:=>|->|→)?\s*alpha", text)
    lower_le_match = re.search(r"price\s*<\s*=\s*(\d+(?:\.\d+)?)\s*(?:=>|->|→)?\s*risk", text)
    upper_match = re.search(r"price\s*>\s*(\d+(?:\.\d+)?)\s*(?:=>|->|→)?\s*alpha", text)
    lower_match = re.search(r"price\s*<\s*(\d+(?:\.\d+)?)\s*(?:=>|->|→)?\s*risk", text)
    otherwise_idle = bool(re.search(r"otherwise\s*(?:=>|->|→)?\s*idle", text))

    if not upper_ge_match and not lower_le_match and not upper_match and not lower_match and not otherwise_idle:
        return None

    if upper_ge_match and price_usd >= float(upper_ge_match.group(1)):
        return "alpha"
    if lower_le_match and price_usd <= float(lower_le_match.group(1)):
        return "risk"
    if upper_match and price_usd > float(upper_match.group(1)):
        return "alpha"
    if lower_match and price_usd < float(lower_match.group(1)):
        return "risk"
    if otherwise_idle:
        return "idle"
    return None


def _build_ai_rule_engine_input(snap: dict[str, Any]) -> str:
    symbol = str(snap.get("symbol") or "mETH")
    price_usd = _safe_float(snap.get("price_usd"))
    price_change_24h = _safe_float(snap.get("price_change_24h"))
    buys_m5 = int(snap.get("buys_m5") or 0)
    sells_m5 = int(snap.get("sells_m5") or 0)
    vol_m5 = _safe_float(snap.get("vol_m5"))

    total_trades_m5 = buys_m5 + sells_m5
    buy_sell_ratio = (buys_m5 / sells_m5) if sells_m5 > 0 else float(buys_m5)
    net_order_flow = buys_m5 - sells_m5

    volume_baseline = None
    volume_spike_pct = None
    if symbol in VOLUME_HISTORY and VOLUME_HISTORY[symbol]:
        baseline = mean(VOLUME_HISTORY[symbol])
        if baseline > 0:
            volume_baseline = baseline
            volume_spike_pct = ((vol_m5 - baseline) / baseline) * 100

    recent_history = list(DECISION_HISTORY)[:3]
    recent_lines = []
    for idx, item in enumerate(recent_history, start=1):
        recent_lines.append(
            f"{idx}) {item.get('state')} | {item.get('token')} | {item.get('metric')} | ruleApplied={item.get('ruleApplied')}"
        )
    recent_block = "\n".join(recent_lines) if recent_lines else "none"

    features = [
        f"token: {symbol}",
        f"price_usd: {price_usd:.4f}",
        f"price_change_24h_pct: {price_change_24h:.2f}",
        f"volume_m5_usd: {vol_m5:.2f}",
        f"buys_m5: {buys_m5}",
        f"sells_m5: {sells_m5}",
        f"total_trades_m5: {total_trades_m5}",
        f"buy_sell_ratio: {buy_sell_ratio:.3f}",
        f"net_order_flow: {net_order_flow}",
    ]

    if volume_baseline is not None:
        features.append(f"volume_baseline_usd: {volume_baseline:.2f}")
    if volume_spike_pct is not None:
        features.append(f"volume_spike_pct_vs_baseline: {volume_spike_pct:.2f}")

    features.append(f"last_chain_state: {LAST_CHAIN_WRITE.lastState or 'none'}")
    features.append(f"last_chain_success: {LAST_CHAIN_WRITE.lastSuccess}")

    return (
        "Market feature pack:\n"
        + "\n".join(features)
        + "\n\nRecent decision history (latest first):\n"
        + recent_block
    )


def infer_state(symbol: str, vol_m5: float, buys_m5: int, sells_m5: int) -> tuple[str, str]:
    # Basic mode: convert raw market activity into idle / alpha / risk.
    history = VOLUME_HISTORY[symbol]
    baseline = mean(history) if history else vol_m5
    baseline = baseline if baseline > 0 else 1.0

    change_pct = ((vol_m5 - baseline) / baseline) * 100
    metric = "N/A"

    if change_pct >= 250 or sells_m5 >= max(6, buys_m5 * 3):
        state = "risk"
    elif change_pct >= 120 or buys_m5 >= 8:
        state = "alpha"
    else:
        state = "idle"

    history.append(vol_m5)
    return state, metric


async def generate_cat_message(state: str, token: str, metric: str) -> str:
    # Basic mode: fallback copy always works.
    # Phase 2 mode: if OpenAI is configured, generate a short cat message.
    fallback = {
        "idle": _fallback_message("idle", token),
        "alpha": _fallback_message("alpha", token),
        "risk": _fallback_message("risk", token),
    }

    metric_hint = metric if metric else "No signal"
    enriched_fallback = {
        "idle": _short_message("idle", token, metric_hint),
        "alpha": _short_message("alpha", token, metric_hint),
        "risk": _short_message("risk", token, metric_hint),
    }

    if not ai_client:
        return enriched_fallback[state]

    recent_messages: list[str] = []
    for row in list(DECISION_HISTORY)[:6]:
        msg = str(row.get("message") or "").strip()
        if msg:
            recent_messages.append(msg)

    try:
        text = ""
        for attempt in range(4):
            rewrite_hint = ""
            if attempt >= 1:
                rewrite_hint = (
                    "Hard constraint: do not reuse phrasing from recent messages. "
                    "Use different opening words, verbs, and sentence rhythm."
                )

            style_hint = _cat_style_hint(state, len(DECISION_HISTORY) + attempt)
            banned = " || ".join(recent_messages[:3]) if recent_messages else "none"

            response = await asyncio.to_thread(
                ai_client.chat.completions.create,
                model=AI_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a tsundere market cat reporter. "
                            "Output exactly one concise English line. "
                            "Write like a human, not like a dashboard. "
                            "Use distinct tone by state: idle=watchful, alpha=confident, risk=warning. "
                            "Use cat-like wording naturally (paws, whiskers, purr, hiss, meow) without overdoing it. "
                            "Do not begin with labels like 'Bullish:', 'Risk:', or 'Watching:'. "
                            "Do not mention state names directly. "
                            "Avoid repeating wording from recent lines."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"state:{state}; token:{token}; key_metrics:{metric_hint}; "
                            f"recent_messages_to_avoid:{banned}; style_hint:{style_hint}; "
                            f"nonce:{datetime.now(timezone.utc).isoformat()}; "
                            f"{rewrite_hint}"
                        ),
                    },
                ],
                max_tokens=120,
                temperature=1.1,
            )
            text = _extract_ai_text(response.choices[0].message.content).strip()
            if len(text) < 4:
                text = ""
            if text and _is_low_quality_ai_text(text):
                if attempt < 3:
                    continue
                text = ""

            if text and _is_repetitive_message(text, recent_messages[:4]):
                if attempt < 3:
                    continue
                text = _short_message(state, token, metric_hint)

            break

        if not text:
            logger.warning("AI returned empty content provider=%s model=%s", AI_PROVIDER, AI_MODEL)
        return text[:180] if text else enriched_fallback[state]
    except Exception as exc:
        logger.exception("generate_cat_message failed: %s", exc)
        return enriched_fallback[state]


async def evaluate_with_ai_rules(snap: dict[str, Any], rules: str) -> tuple[str, str]:
    """Ask AI to apply user-defined rules to the latest candle snapshot.
    Returns (state, message). Falls back to rule-free inference on error."""
    ai_feature_pack = _build_ai_rule_engine_input(snap)
    price_usd = _safe_float(snap.get("price_usd"))
    forced_state = _evaluate_price_threshold_rules(rules, price_usd)
    user_content = (
        f"User rules:\n{rules}\n\n"
        f"{ai_feature_pack}\n\n"
        f"forced_state_from_rule_parser: {forced_state or 'none'}\n"
        f"analysis_id: {datetime.now(timezone.utc).strftime('%H%M%S%f')}"
    )

    if not ai_client:
        fb_state = forced_state or evaluate_meth_state(snap)
        symbol = str(snap.get("symbol") or "mETH")
        return fb_state, _short_message(fb_state, symbol, build_rich_metric(snap))

    try:
        response = await asyncio.to_thread(
            ai_client.chat.completions.create,
            model=AI_MODEL,
            messages=[
                {"role": "system", "content": RULE_ENGINE_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            max_tokens=220,
            temperature=0.2,
        )
        text = _extract_ai_text(response.choices[0].message.content)
        result = _parse_ai_json_response(text)
        if not result:
            logger.warning("Rule engine JSON parse failed. raw=%s", (text or "")[:500])
            raise ValueError("rule-engine-json-parse-failed")
        ai_state = result.get("state", "idle")
        if forced_state in {"idle", "alpha", "risk"}:
            ai_state = forced_state
        if ai_state not in {"idle", "alpha", "risk"}:
            ai_state = "idle"
        symbol = str(snap.get("symbol") or "mETH")
        ai_msg = await generate_cat_message(ai_state, symbol, build_rich_metric(snap))
        if not ai_msg:
            ai_msg = _short_message(ai_state, symbol, build_rich_metric(snap))
        return ai_state, ai_msg
    except Exception as exc:
        logger.exception("evaluate_with_ai_rules failed: %s", exc)
        fb_state = forced_state or evaluate_meth_state(snap)
        symbol = str(snap.get("symbol") or "mETH")
        fb_metric = build_rich_metric(snap)
        if ai_client:
            try:
                ai_fallback = await generate_cat_message(fb_state, symbol, fb_metric)
                if ai_fallback:
                    return fb_state, ai_fallback
            except Exception as fallback_exc:
                logger.warning("AI fallback message generation failed: %s", fallback_exc)
        return fb_state, _short_message(fb_state, symbol, fb_metric)


async def refresh_widget_cache() -> WidgetPayload:
    global WIDGET_CACHE, LAST_CHAIN_WRITE, DECISION_HISTORY

    # Basic mode: poll sources, infer state, and update in-memory cache.
    # Phase 2 mode: if the state changes, optionally generate AI text and write
    # the new state/message to the smart contract.
    previous_state = WIDGET_CACHE.state
    best_signal: tuple[str, str, str] | None = None
    best_snap: dict[str, Any] = {}

    for symbol, address in WATCH_TOKENS.items():
        try:
            snap = await fetch_token_snapshot(symbol, address)

            TOKEN_SOURCE_CACHE[symbol] = TokenSourceStatus(
                symbol=symbol,
                address=address,
                ok=bool(snap.get("ok")),
                httpStatus=snap.get("http_status"),
                volM5=float(snap.get("vol_m5") or 0.0),
                buysM5=int(snap.get("buys_m5") or 0),
                sellsM5=int(snap.get("sells_m5") or 0),
                priceChange24h=float(snap.get("price_change_24h") or 0.0),
                lastFetchAt=_now_iso(),
                error=snap.get("error"),
            )

            if not snap.get("ok"):
                continue

            if symbol.lower() == "meth":
                state = evaluate_meth_state(snap)
                metric = build_meth_metric(snap, state)
            else:
                state, _ = infer_state(
                    symbol=snap["symbol"],
                    vol_m5=snap["vol_m5"],
                    buys_m5=snap["buys_m5"],
                    sells_m5=snap["sells_m5"],
                )
                metric = build_price_metric(snap)

            priority = {"idle": 0, "alpha": 1, "risk": 2}
            if symbol.lower() == "meth" and state in {"alpha", "risk"}:
                best_signal = (state, symbol, metric)
                best_snap = snap
                break

            if (not best_signal) or priority[state] > priority[best_signal[0]]:
                best_signal = (state, symbol, metric)
                best_snap = snap
        except Exception as exc:
            TOKEN_SOURCE_CACHE[symbol] = TokenSourceStatus(
                symbol=symbol,
                address=address,
                ok=False,
                httpStatus=None,
                volM5=0.0,
                buysM5=0,
                sellsM5=0,
                priceChange24h=0.0,
                lastFetchAt=_now_iso(),
                error=str(exc),
            )
            continue

    if not best_signal:
        best_signal = ("idle", next(iter(WATCH_TOKENS)), "N/A")

    state, token, metric = best_signal
    message = WIDGET_CACHE.message
    rule_applied = False
    current_tx_hash: str | None = None

    # Rule engine: if user has saved rules, let AI decide state + message every 15m
    if USER_RULES.strip() and best_snap:
        try:
            state, message = await evaluate_with_ai_rules(best_snap, USER_RULES)
            metric = build_price_metric(best_snap) or metric
            rule_applied = True
            logger.info("Rule engine: state=%s rule_applied=True", state)
        except Exception as exc:
            logger.exception("Rule engine evaluation failed, falling back: %s", exc)

    if state != previous_state:
        if not rule_applied:
            message = await generate_cat_message(state, token, metric)
        LAST_CHAIN_WRITE = ChainWriteStatus(
            enabled=CHAIN_WRITE_ENABLED,
            chainId=CHAIN_ID,
            contractAddress=CONTRACT_ADDRESS,
            walletAddress=web3_account.address if web3_account else "",
            lastAttemptAt=_now_iso(),
            lastState=state,
            lastMessage=message,
            lastTxHash=LAST_CHAIN_WRITE.lastTxHash,
            lastSuccess=False,
            lastError=None,
        )
        try:
            tx_hash = await asyncio.to_thread(write_state_to_chain, state, message)
            if tx_hash:
                current_tx_hash = tx_hash
                LAST_CHAIN_WRITE = ChainWriteStatus(
                    enabled=CHAIN_WRITE_ENABLED,
                    chainId=CHAIN_ID,
                    contractAddress=CONTRACT_ADDRESS,
                    walletAddress=web3_account.address if web3_account else "",
                    lastAttemptAt=_now_iso(),
                    lastState=state,
                    lastMessage=message,
                    lastTxHash=tx_hash,
                    lastSuccess=True,
                    lastError=None,
                )
                logger.info("State written on-chain state=%s tx=%s", state, tx_hash)
            else:
                LAST_CHAIN_WRITE = ChainWriteStatus(
                    enabled=CHAIN_WRITE_ENABLED,
                    chainId=CHAIN_ID,
                    contractAddress=CONTRACT_ADDRESS,
                    walletAddress=web3_account.address if web3_account else "",
                    lastAttemptAt=_now_iso(),
                    lastState=state,
                    lastMessage=message,
                    lastTxHash=LAST_CHAIN_WRITE.lastTxHash,
                    lastSuccess=False,
                    lastError="state-changed-but-no-tx",
                )
                logger.warning("State changed but no on-chain tx was sent")
        except Exception as exc:
            LAST_CHAIN_WRITE = ChainWriteStatus(
                enabled=CHAIN_WRITE_ENABLED,
                chainId=CHAIN_ID,
                contractAddress=CONTRACT_ADDRESS,
                walletAddress=web3_account.address if web3_account else "",
                lastAttemptAt=_now_iso(),
                lastState=state,
                lastMessage=message,
                lastTxHash=LAST_CHAIN_WRITE.lastTxHash,
                lastSuccess=False,
                lastError=str(exc),
            )
            logger.exception("On-chain write failed but cache will still update: %s", exc)
    elif not rule_applied:
        if ai_client and _should_retry_ai_message(state, token, metric, message):
            message = await generate_cat_message(state, token, metric)
        elif not message:
            message = await generate_cat_message(state, token, metric)

    # Always record every 15m refresh in history
    DECISION_HISTORY.appendleft({
        "timestamp": _now_iso(),
        "state": state,
        "message": message,
        "metric": metric,
        "token": token,
        "priceUsd": _safe_float(best_snap.get("price_usd")),
        "priceChange24h": _safe_float(best_snap.get("price_change_24h")),
        "buysM5": int(best_snap.get("buys_m5") or 0),
        "sellsM5": int(best_snap.get("sells_m5") or 0),
        "volM5": _safe_float(best_snap.get("vol_m5")),
        "ruleApplied": rule_applied,
        "source": "live",
        "txHash": current_tx_hash,
        "chainId": CHAIN_ID if current_tx_hash else None,
    })

    updated = WidgetPayload(
        state=state,
        message=message,
        metric=metric,
        token=token,
        updatedAt=_now_iso(),
    )

    WIDGET_CACHE = updated
    return updated


app = FastAPI(title="On-chain Lucky Cat API", version="0.1.0")

# Shared API layer for both modes
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup() -> None:
    # Basic mode startup: warm cache once, then start scheduler.
    await refresh_widget_cache()
    scheduler.add_job(
        refresh_widget_cache,
        "cron",
        minute=REFRESH_CRON_MINUTES,
        second=0,
        id="refresh",
        replace_existing=True,
    )
    scheduler.start()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
    await http_client.aclose()


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "updatedAt": WIDGET_CACHE.updatedAt}


@app.get("/api/widget/latest", response_model=WidgetPayload)
async def get_widget_latest() -> WidgetPayload:
    # Basic mode frontend endpoint
    return WIDGET_CACHE


@app.post("/api/widget/refresh", response_model=WidgetPayload)
async def force_refresh() -> WidgetPayload:
    # Manual trigger for demos and testing
    try:
        return await refresh_widget_cache()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Refresh failed: {exc}") from exc


@app.get("/api/widget/debug-source", response_model=DebugSourcePayload)
async def debug_source() -> DebugSourcePayload:
    # Inspect upstream fetch results without changing widget state
    return DebugSourcePayload(
        geckoNetwork=GECKO_NETWORK,
        pollSeconds=POLL_SECONDS,
        updatedAt=WIDGET_CACHE.updatedAt,
        sources=list(TOKEN_SOURCE_CACHE.values()),
    )


@app.get("/api/widget/chain-status", response_model=ChainWriteStatus)
async def chain_status() -> ChainWriteStatus:
    # Returns latest on-chain write attempt, including tx hash when available.
    return LAST_CHAIN_WRITE


@app.get("/api/widget/user-rules")
async def get_user_rules() -> dict[str, Any]:
    return {"rules": USER_RULES}


@app.post("/api/widget/user-rules")
async def save_user_rules(payload: UserRulesRequest) -> dict[str, Any]:
    global USER_RULES
    USER_RULES = (payload.rules or "").strip()
    logger.info("User rules updated: %s chars", len(USER_RULES))
    return {"ok": True, "rules": USER_RULES}


@app.get("/api/widget/history")
async def get_history() -> dict[str, Any]:
    return {"entries": list(DECISION_HISTORY)}


@app.post("/api/widget/demo-write")
async def demo_write(payload: DemoWriteRequest) -> dict[str, Any]:
    """Demo mode: uses preset fake candle data but runs the full real pipeline —
    AI message generation (with user rules if set), on-chain write, history log.
    """
    global LAST_CHAIN_WRITE, DECISION_HISTORY

    def record_demo_history(tx_hash: str | None) -> None:
        DECISION_HISTORY.appendleft({
            "timestamp": _now_iso(),
            "state": state,
            "message": message,
            "metric": metric,
            "token": token,
            "priceUsd": _safe_float(fake_snap.get("price_usd")),
            "priceChange24h": _safe_float(fake_snap.get("price_change_24h")),
            "buysM5": int(fake_snap.get("buys_m5") or 0),
            "sellsM5": int(fake_snap.get("sells_m5") or 0),
            "volM5": _safe_float(fake_snap.get("vol_m5")),
            "ruleApplied": rule_applied,
            "source": "demo",
            "txHash": tx_hash,
            "chainId": CHAIN_ID if tx_hash else None,
        })

    requested_state = (payload.state or "").strip().lower()
    if requested_state and requested_state not in {"idle", "alpha", "risk"}:
        raise HTTPException(status_code=400, detail="state must be one of idle, alpha, risk")

    # Build fake candle snapshot from explicit price or state preset.
    if payload.priceUsd is not None:
        demo_price = _safe_float(payload.priceUsd)
        fake_snap = dict(DEMO_SNAP_PRESETS["idle"])
        fake_snap["price_usd"] = demo_price
        if demo_price > 4000:
            fake_snap["price_change_24h"] = max(_safe_float(fake_snap.get("price_change_24h")), 5.2)
            fake_snap["buys_m5"] = max(int(fake_snap.get("buys_m5") or 0), 10)
            fake_snap["sells_m5"] = min(int(fake_snap.get("sells_m5") or 0), 3)
            fake_snap["vol_m5"] = max(_safe_float(fake_snap.get("vol_m5")), 42000.0)
        elif demo_price < 2000:
            fake_snap["price_change_24h"] = min(_safe_float(fake_snap.get("price_change_24h")), -5.2)
            fake_snap["buys_m5"] = min(int(fake_snap.get("buys_m5") or 0), 3)
            fake_snap["sells_m5"] = max(int(fake_snap.get("sells_m5") or 0), 10)
            fake_snap["vol_m5"] = max(_safe_float(fake_snap.get("vol_m5")), 35000.0)
        state = "idle"
    else:
        state = requested_state or "idle"
        fake_snap = dict(DEMO_SNAP_PRESETS.get(state, DEMO_SNAP_PRESETS["idle"]))
    token = fake_snap.get("symbol", "mETH")
    metric = build_rich_metric(fake_snap)

    # Run through the real AI pipeline (always use rule engine in demo) ───────
    effective_rules = USER_RULES.strip() or DEFAULT_DEMO_RULES
    rule_applied = True
    try:
        state, message = await evaluate_with_ai_rules(fake_snap, effective_rules)
        metric = build_rich_metric(fake_snap) or metric
        logger.info("Demo rule engine: state=%s using_default_rules=%s", state, not bool(USER_RULES.strip()))
    except Exception as exc:
        logger.exception("Demo evaluate_with_ai_rules failed: %s", exc)
        # Fallback still carries richer metric context.
        message = await generate_cat_message(state, token, metric)

    # On-chain write ───────────────────────────────────────────────────────────
    if not CHAIN_WRITE_ENABLED:
        record_demo_history(None)
        LAST_CHAIN_WRITE = ChainWriteStatus(
            enabled=False,
            chainId=CHAIN_ID,
            contractAddress=CONTRACT_ADDRESS,
            walletAddress=web3_account.address if web3_account else "",
            lastAttemptAt=_now_iso(),
            lastState=state,
            lastMessage=message,
            lastTxHash=LAST_CHAIN_WRITE.lastTxHash,
            lastSuccess=False,
            lastError="chain-write-disabled",
        )
        return {
            "ok": False,
            "reason": "chain-write-disabled",
            "state": state,
            "message": message,
            "metric": metric,
            "txHash": None,
            "chainStatus": LAST_CHAIN_WRITE.model_dump(),
            "ruleApplied": rule_applied,
        }

    LAST_CHAIN_WRITE = ChainWriteStatus(
        enabled=CHAIN_WRITE_ENABLED,
        chainId=CHAIN_ID,
        contractAddress=CONTRACT_ADDRESS,
        walletAddress=web3_account.address if web3_account else "",
        lastAttemptAt=_now_iso(),
        lastState=state,
        lastMessage=message,
        lastTxHash=LAST_CHAIN_WRITE.lastTxHash,
        lastSuccess=False,
        lastError=None,
    )

    tx_hash = await asyncio.to_thread(write_state_to_chain, state, message)
    if tx_hash:
        record_demo_history(tx_hash)
        LAST_CHAIN_WRITE = ChainWriteStatus(
            enabled=CHAIN_WRITE_ENABLED,
            chainId=CHAIN_ID,
            contractAddress=CONTRACT_ADDRESS,
            walletAddress=web3_account.address if web3_account else "",
            lastAttemptAt=_now_iso(),
            lastState=state,
            lastMessage=message,
            lastTxHash=tx_hash,
            lastSuccess=True,
            lastError=None,
        )
        return {
            "ok": True,
            "reason": None,
            "state": state,
            "message": message,
            "metric": metric,
            "txHash": tx_hash,
            "chainStatus": LAST_CHAIN_WRITE.model_dump(),
            "ruleApplied": rule_applied,
        }

    record_demo_history(None)
    LAST_CHAIN_WRITE = ChainWriteStatus(
        enabled=CHAIN_WRITE_ENABLED,
        chainId=CHAIN_ID,
        contractAddress=CONTRACT_ADDRESS,
        walletAddress=web3_account.address if web3_account else "",
        lastAttemptAt=_now_iso(),
        lastState=state,
        lastMessage=message,
        lastTxHash=LAST_CHAIN_WRITE.lastTxHash,
        lastSuccess=False,
        lastError="send-failed",
    )
    return {
        "ok": False,
        "reason": "send-failed",
        "state": state,
        "message": message,
        "metric": metric,
        "txHash": None,
        "chainStatus": LAST_CHAIN_WRITE.model_dump(),
        "ruleApplied": rule_applied,
    }

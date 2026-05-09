# Lucky Cat Backend (FastAPI)

Backend service for the Lucky Cat widget.
It handles market data fetch, rule evaluation, AI message generation, and on-chain state updates.

## Architecture Snapshot

- API framework: FastAPI
- Scheduler: APScheduler (aligned refresh cadence)
- Chain integration: web3.py (Mantle Sepolia)
- Contract call: `updateState(state, message)` on `LuckyCatState`

## Deployed Contract (Mantle Sepolia)

- Chain ID: `5003`
- Contract: `0x65108485127C78D50eD3e9651a45Bc80D2A0a195`
- Explorer: `https://sepolia.mantlescan.xyz/address/0x65108485127C78D50eD3e9651a45Bc80D2A0a195#code`

## 1) Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## 2) Configure

Edit `.env`:

- `WATCH_TOKENS`: Mantle token symbol and address list, format:
  - `mETH:0x...,USDY:0x...`
- `REFRESH_CRON_MINUTES`: cron minute list for aligned refresh (default `1,16,31,46`)
- `OHLCV_AGGREGATE_MINUTES`: GeckoTerminal minute OHLCV aggregate (default `15`)
- `AI_PROVIDER`: `gemini` or `openai`
- `GEMINI_API_KEY`: optional, used to generate a short cat message when state changes
- `MANTLE_RPC_URL`: Mantle RPC endpoint
- `CHAIN_ID`: Mantle network chain id
- `PRIVATE_KEY`: owner wallet private key used to sign the update transaction
- `CONTRACT_ADDRESS`: deployed `LuckyCatState` contract address (auto-filled by deploy script)

Testnet quick values:

- `MANTLE_RPC_URL=https://rpc.sepolia.mantle.xyz`
- `CHAIN_ID=5003`

## 3) Deploy Contract (On-chain)

```bash
cd backend
source .venv/bin/activate
pip install -r requirements.txt
python scripts/deploy_contract.py
```

This will:

- compile `contracts/LuckyCatState.sol`
- deploy to Mantle using your `.env` credentials
- write deployed address into `.env` as `CONTRACT_ADDRESS`

## 4) Run

```bash
uvicorn app.main:app --reload --port 8000
```

When `MANTLE_RPC_URL`, `PRIVATE_KEY`, and `CONTRACT_ADDRESS` are all present,
backend startup log should show `chain_write_enabled=True`.

Scheduler behavior: backend warms cache once on startup, then runs aligned cron refreshes
at minute `1,16,31,46` by default so 15m candles are fetched shortly after candle close.

On-chain write policy:

- Market data refresh uses 15m cadence.
- Smart contract writes are event-driven, not time-driven.
- A transaction is sent only when computed `state` changes (e.g. `idle -> alpha`, `alpha -> risk`).
- If `state` stays the same, backend updates cache/UI only and skips on-chain tx to save gas.
- Latest write attempt and `txHash` can be checked via `GET /api/widget/chain-status`.

Demo mode + on-chain behavior:

- Frontend demo buttons now send prices (`2000 / 3000 / 4000`) to `POST /api/widget/demo-write`.
- Backend converts price into fake snapshot context, runs rule-engine AI evaluation, then writes on-chain.
- This means demo interactions can intentionally create real testnet transactions for presentation.
- If you want demo interactions without chain writes, disable chain write by removing `PRIVATE_KEY` or `CONTRACT_ADDRESS` in `.env` and restart backend.

What is written on-chain:

- Contract function: `updateState(string _state, string _message)`
- Stored fields in `LuckyCatState.sol`:
  - `currentState`
  - `lastAIMessage`
  - `lastUpdated` (block timestamp)
- Event emitted: `StateUpdated(state, message, updatedAt)`
- This is a contract state update call (`value = 0`), not a token transfer.

How `message` is generated:

- Live refresh:
  - if user rules exist, backend uses rule-engine AI output (`state + message`)
  - if user rules are empty, backend uses default state inference + AI/fallback copy
- Demo refresh:
  - always runs through rule engine (user rule or default demo rule)
  - AI receives expanded feature pack (price, 24h%, B/S, volume, derived signals, recent history)
  - if AI output fails, backend falls back to internal message

Rule engine notes:

- User rules are highest priority in AI prompt.
- Price threshold parser supports `>`, `<`, `>=`, `<=` patterns.
- Default demo rules:
  - `price >= 4000 => alpha`
  - `price <= 2000 => risk`
  - `otherwise => idle`

Explorer note (important for demo):

- Transaction may be confirmed on RPC first, while explorer can briefly show "not found" due to indexing delay.
- If explorer lags, verify with RPC directly:

```bash
curl -s -X POST https://rpc.sepolia.mantle.xyz \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_getTransactionReceipt","params":["<TX_HASH>"],"id":1}'
```

- `result != null` and `status = 0x1` means the tx is already confirmed on-chain.

## 5) API

- `GET /health`
- `GET /api/widget/latest`
- `POST /api/widget/refresh`
- `GET /api/widget/debug-source`
- `GET /api/widget/chain-status` (latest on-chain write attempt + tx hash)
- `GET /api/widget/user-rules`
- `POST /api/widget/user-rules`
- `GET /api/widget/history`
- `POST /api/widget/demo-write` (manual demo trigger with price/state input)

`POST /api/widget/demo-write` request body:

```json
{
  "priceUsd": 2000,
  "state": "idle | alpha | risk (optional)",
  "message": "optional custom message"
}
```

History payload fields (`GET /api/widget/history`):

- `timestamp`
- `state`
- `message`
- `metric`
- `token`
- `priceUsd`
- `priceChange24h`
- `buysM5`
- `sellsM5`
- `volM5`
- `ruleApplied`
- `source` (`live` or `demo`)
- `txHash` (nullable)
- `chainId` (nullable)

### Response Example (`POST /api/widget/demo-write`)

```json
{
  "ok": true,
  "reason": null,
  "state": "idle",
  "message": "I'm watching mETH for you...",
  "metric": "$3,000.0 | 24h -0.80% | B/S 4/3 | Vol $8,500",
  "txHash": "0x63ef30fc04981a3395c1c3d42ff19d04c8ab3e8689bd5bf940d31f78c5ddc29d",
  "chainStatus": {
    "enabled": true,
    "chainId": 5003,
    "contractAddress": "0x65108485127C78D50eD3e9651a45Bc80D2A0a195",
    "walletAddress": "0x4d9607cF947a47518DdeCAe3fd78d27DFB8fd003",
    "lastAttemptAt": "2026-05-09T15:44:30.959253+00:00",
    "lastState": "idle",
    "lastMessage": "I'm watching mETH for you...",
    "lastTxHash": "0x63ef30fc04981a3395c1c3d42ff19d04c8ab3e8689bd5bf940d31f78c5ddc29d",
    "lastSuccess": true,
    "lastError": null
  },
  "ruleApplied": true
}
```

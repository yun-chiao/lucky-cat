# Lucky Cat: AI-Driven On-Chain Widget

Lucky Cat is a full-stack hackathon prototype that combines:

- A React widget UI (Vite + Tailwind + Framer Motion)
- A FastAPI backend with market polling and AI message generation
- Rule-based signal decisions (`idle`, `alpha`, `risk`)
- On-chain state writes to a verified smart contract on Mantle Sepolia

This repository is open source and intended as a reproducible demo project.

## Live Demo

- Demo app: `https://lucky-cat-ze6o.vercel.app`
- Backend API: `https://lucky-cat.onrender.com`

## 1) Setup Instructions

### Frontend

From project root:

```bash
npm install
npm run dev
```

The frontend runs at:

- `http://localhost:5173`

### Backend

From `backend`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Run backend:

```bash
uvicorn app.main:app --reload --port 8000
```

Backend endpoint:

- `http://localhost:8000`

## 2) Architecture Overview

### Frontend Layer

- `src/App.jsx`
	- Demo mode / live mode switch
	- Rule editor UI (natural-language style rules)
	- Demo trigger buttons and manual refresh controls
	- History table with source and tx hash details
	- Explorer link rendering for on-chain verification
- `src/components/LuckyCatWidget.jsx`
	- Main widget card (`idle`, `alpha`, `risk`)
	- Hero metric + human-readable cat message
	- State-driven visual updates for presentation

### Backend Layer

- `backend/app/main.py`
	- Fetches market snapshots
	- Applies rule engine + AI text generation
	- Sends on-chain write (`updateState`) when required
	- Exposes API endpoints for widget data, history, rules, and demo writes

### On-Chain Layer

- `backend/contracts/LuckyCatState.sol`
	- Stores:
		- `currentState`
		- `lastAIMessage`
		- `lastUpdated`
	- Emits event:
		- `StateUpdated(state, message, updatedAt)`

### Data Flow (Demo)

1. User clicks a demo price button (`2000`, `3000`, `4000`).
2. Frontend calls `POST /api/widget/demo-write`.
3. Backend builds a demo market snapshot from that input.
4. User rules (or fallback default rules) are included in the AI rule prompt.
5. AI rule engine returns a decision (`idle`, `alpha`, `risk`) and message.
6. Backend validates/parses the result and applies fallback logic only if needed.
7. Backend writes `updateState(state, message)` on-chain when chain write is enabled.
8. Frontend renders tx hash, explorer link, and history record with `source=demo`.

## 3) Frontend Features (What Was Built)

- Interactive Lucky Cat widget with three market states:
	- `idle` (neutral watch mode)
	- `alpha` (bullish signal)
	- `risk` (defensive signal)
- Rule management panel:
	- User can define custom rules such as `price > 4200 -> alpha`.
	- Rules are stored and reused by backend evaluation endpoints.
- Demo simulation controls:
	- One-click demo prices allow deterministic presentation scenarios.
	- Demo calls can generate real testnet transactions (if chain env is enabled).
- Traceable history UI:
	- Shows state, message, metric, source (`live` or `demo`), and tx hash.
	- Makes it easy to prove that UI decisions were written on-chain.

## 4) AI Rule Engine to On-Chain Pipeline

This project does not use a single static hardcoded decision path.
Instead, rules and market context are evaluated through an AI-assisted rule engine:

1. Backend gathers context (price, 24h change, buy/sell pressure, volume, recent history).
2. It injects user rule text as high-priority instruction into the prompt.
3. AI produces structured decision output (`state`, `message`).
4. Backend parser validates the output format and normalizes state values.
5. If response is invalid, backend applies deterministic fallback and retries where configured.
6. Final decision is persisted in cache/history and optionally written to smart contract.

Result: rule intent is preserved, while final wording remains dynamic and readable.

## 5) Deployed Contract Address

- Network: Mantle Sepolia Testnet (`chainId = 5003`)
- Verified contract address:
	- `0x65108485127C78D50eD3e9651a45Bc80D2A0a195`
- Explorer:
	- `https://sepolia.mantlescan.xyz/address/0x65108485127C78D50eD3e9651a45Bc80D2A0a195#code`

## 6) Key API Endpoints

Core endpoints used in judge-facing flows:

- `GET /api/widget/latest` (load current widget state)
- `GET /api/widget/history` (show decision and tx trace history)
- `GET /api/widget/user-rules` (load saved user rules)
- `POST /api/widget/user-rules` (save/update user rules)
- `POST /api/widget/demo-write` (run demo decision and optionally write on-chain)

Optional operational/diagnostic endpoints:

- `GET /health` (service liveness check)
- `POST /api/widget/refresh` (manual refresh trigger)
- `GET /api/widget/chain-status` (latest chain write status)

## 7) Notes for Evaluators

- This repository includes setup instructions, architecture overview, and deployed contract address.
- Demo mode can trigger real on-chain writes when chain config is enabled.
- Rule text is fed into the AI rule engine before final state selection.
- Final state + message are written on-chain through contract `updateState`.
- Explorer indexing can lag briefly; if tx is not visible immediately, wait and refresh.
- Backend documentation is in `backend/README.md` with environment and API details.

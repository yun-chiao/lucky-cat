# On-chain Lucky Cat Demo Flow

## 1. Opening

- This is On-chain Lucky Cat, a consumer-facing Web3 signal widget for Mantle.
- The cat stays calm when the market is quiet and reacts when on-chain behavior changes.

## 2. What the user sees

- Left side: the cat character changes mood.
- Right side: only three pieces of information are shown.
  - Emotion: what happened
  - Hero Metric: why the cat reacted
  - Context: token + freshness

## 3. Data pipeline

- Backend polls GeckoTerminal every 45 seconds.
- Current focus token is mETH on Mantle.
- Backend picks the largest pool and reads short-window activity.
- It converts raw market activity into three states:
  - idle
  - alpha
  - risk

## 4. API endpoints to show live

- Health: `GET /health`
- Latest widget data: `GET /api/widget/latest`
- Force refresh: `POST /api/widget/refresh`
- Source debug: `GET /api/widget/debug-source`

## 5. Demo script

1. Open the widget UI.
2. Show that the frontend can read live backend data.
3. Open `/api/widget/debug-source` and explain:
   - HTTP status from GeckoTerminal
   - volM5
   - buysM5
   - sellsM5
4. Trigger `/api/widget/refresh`.
5. Explain how backend maps the result into `idle`, `alpha`, or `risk`.
6. Switch to mock mode if needed to demonstrate all three visual states quickly.

## 6. What to say if values are zero

- m5 means the latest 5-minute activity window.
- If recent trades are quiet, `volM5`, `buysM5`, and `sellsM5` can all be zero.
- This does not mean the API is broken; it means the market is quiet in that time slice.

## 7. Current project status

- Frontend widget is ready.
- Backend live feed is wired.
- Debug visibility is ready.
- Smart contract / AI-triggered on-chain write is the next expansion step.

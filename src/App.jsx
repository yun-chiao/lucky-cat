import { useCallback, useEffect, useState } from 'react';
import LuckyCatWidget from './components/LuckyCatWidget';

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');

const MOCK_FEED = {
  idle: {
    emotion: "I'm watching mETH for you...",
    heroMetric: '$2,529.2',
    token: 'mETH',
    updatedAt: '10s ago'
  },
  alpha: {
    emotion: 'Momentum detected on mETH.',
    heroMetric: '$2,529.2',
    token: 'mETH',
    updatedAt: '6s ago'
  },
  risk: {
    emotion: 'Heavy sell pressure detected on mETH.',
    heroMetric: '$2,529.2',
    token: 'mETH',
    updatedAt: '4s ago'
  }
};

const MOCK_CAT_IMAGES = {
  idle: '/cats/idle.png',
  alpha: '/cats/alpha.png',
  risk: '/cats/risk.png'
};

const TOKEN_ICONS = {
  meth: '/tokens/meth.png',
  usdy: '/tokens/usdy.svg'
};

const DEMO_BACKEND_PRESETS = {
  idle: {
    state: 'idle',
    message: "I'm watching mETH for you...",
    metric: '$2,529.2',
    token: 'mETH'
  },
  alpha: {
    state: 'alpha',
    message: 'Momentum detected on mETH.',
    metric: '$2,548.7',
    token: 'mETH'
  },
  risk: {
    state: 'risk',
    message: 'Heavy sell pressure detected on mETH.',
    metric: '$2,471.8',
    token: 'mETH'
  }
};

const DEMO_PRICE_BUTTONS = [2000, 3000, 4000];

function toWidgetHeroMetric(metric) {
  if (!metric || typeof metric !== 'string') {
    return MOCK_FEED.idle.heroMetric;
  }
  // Widget shows compact value only; full metric remains in history.
  return metric.split('|')[0].trim() || MOCK_FEED.idle.heroMetric;
}

function toReadableHeadline({ state, message, token }) {
  const fallbackByState = {
    idle: `Whiskers twitching, I am quietly watching ${token || 'mETH'}.`,
    alpha: `The mouse is getting lively around ${token || 'mETH'}.`,
    risk: `The wind is picking up around ${token || 'mETH'}, stay alert.`
  };

  if (!message || typeof message !== 'string') {
    return fallbackByState[state] || fallbackByState.idle;
  }

  // Keep headline human-readable: remove metric chunks and tx-like numeric fragments.
  const firstSegment = message
    .split('|')[0]
    .split(';')[0]
    .replace(/\$?\d+(?:,\d{3})*(?:\.\d+)?%?/g, '')
    .replace(/\b(B\/S|Vol|conf|rule|sig|24h)\b/gi, '')
    .replace(/\s{2,}/g, ' ')
    .trim();

  const cleaned = firstSegment.replace(/[,:;|-]+$/g, '').trim();
  const strippedLabel = cleaned.replace(/^(bullish|watching|risk)\s*:?\s*/i, '').trim();
  const tokenText = (token || 'mETH').toLowerCase();
  const isTemplateHeadline = /^(bullish|watching|risk)\s*:?/i.test(cleaned)
    || strippedLabel.toLowerCase() === tokenText
    || strippedLabel.toLowerCase() === `${tokenText}.`;

  const awkwardFragment = /\bstate\b/i.test(cleaned)
    || /\b(i|we|you|he|she|they|it|to|for|with|and|or|but|so)$/i.test(cleaned)
    || isTemplateHeadline;

  if (cleaned.length >= 8 && !awkwardFragment) {
    return cleaned;
  }

  return fallbackByState[state] || fallbackByState.idle;
}

function toWidgetViewModel(payload) {
  return {
    emotion: toReadableHeadline({
      state: payload.state || 'idle',
      message: payload.message,
      token: payload.token || 'mETH'
    }),
    heroMetric: toWidgetHeroMetric(payload.metric),
    token: payload.token || 'mETH',
    updatedAt: payload.updatedAt
      ? new Date(payload.updatedAt).toLocaleTimeString('en-US', { hour12: false })
      : 'live'
  };
}

async function fetchDemoBackendPayload(mode) {
  await new Promise((resolve) => setTimeout(resolve, 180));
  const preset = DEMO_BACKEND_PRESETS[mode] || DEMO_BACKEND_PRESETS.idle;
  return {
    ...preset,
    updatedAt: new Date().toISOString()
  };
}

const STATE_COLORS = {
  idle: { badge: 'bg-sky-500/15 text-sky-300 border-sky-500/35', label: 'IDLE' },
  alpha: { badge: 'bg-amber-500/15 text-amber-300 border-amber-500/35', label: 'ALPHA' },
  risk: { badge: 'bg-rose-500/15 text-rose-300 border-rose-500/35', label: 'RISK' }
};

function formatTime(iso) {
  try {
    return new Date(iso).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit' });
  } catch {
    return '--:--';
  }
}

export default function App() {
  const [state, setState] = useState('idle');
  const [liveData, setLiveData] = useState(null);
  const [dataMode, setDataMode] = useState('live');
  const [lastError, setLastError] = useState('');
  const [demoScenario, setDemoScenario] = useState(null);
  const [demoPrice, setDemoPrice] = useState(null);
  const [demoChainStatus, setDemoChainStatus] = useState('');
  const [demoChainTxUrl, setDemoChainTxUrl] = useState('');
  const [demoLoading, setDemoLoading] = useState(false);

  // Rule engine
  const DEFAULT_RULES = 'price >= 4000 → alpha\nprice <= 2000 → risk\notherwise → idle';
  const [draftRules, setDraftRules] = useState(DEFAULT_RULES);
  const [savedRules, setSavedRules] = useState('');
  const [rulesSaving, setRulesSaving] = useState(false);
  const [rulesSaveMsg, setRulesSaveMsg] = useState('');

  // Decision history
  const [historyEntries, setHistoryEntries] = useState([]);

  const buildExplorerTxUrl = useCallback((chainId, txHash) => {
    if (!txHash) {
      return '';
    }

    if (Number(chainId) === 5003) {
      return `https://sepolia.mantlescan.xyz/tx/${txHash}`;
    }
    if (Number(chainId) === 5000) {
      return `https://mantlescan.xyz/tx/${txHash}`;
    }
    return '';
  }, []);

  // ── History loader (defined early so triggerDemo can reference it) ───────
  const loadHistory = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE_URL}/api/widget/history`);
      if (!resp.ok) return;
      const { entries } = await resp.json();
      setHistoryEntries(entries || []);
    } catch {}
  }, []);

  // Unified demo trigger: sends {priceUsd} to backend, which runs the full
  // real pipeline (AI message via rules or generate_cat_message, chain write,
  // history log) on top of preset fake candle data, then returns the result.
  const triggerDemo = useCallback(async (priceUsd) => {
    setDemoLoading(true);
    setDemoChainStatus('');
    setDemoChainTxUrl('');
    try {
      const response = await fetch(`${API_BASE_URL}/api/widget/demo-write`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ priceUsd })
      });
      const data = await response.json();
      const backendState = (data.state || 'idle');
      // Update widget with real AI-generated content returned by backend
      setState(backendState);
      setLiveData({
        emotion: toReadableHeadline({
          state: backendState,
          message: data.message,
          token: 'mETH'
        }),
        heroMetric: toWidgetHeroMetric(data.metric),
        token: 'mETH',
        updatedAt: new Date().toLocaleTimeString('en-US', { hour12: false })
      });
      setDataMode('demo-backend');
      setDemoScenario(backendState);
      setDemoPrice(priceUsd);
      setLastError('');
      // Show chain status
      if (data.txHash) {
        const chainId = data.chainStatus?.chainId;
        const shortTx = `${data.txHash.slice(0, 10)}...${data.txHash.slice(-8)}`;
        setDemoChainStatus(`On-chain tx: ${shortTx}`);
        setDemoChainTxUrl(buildExplorerTxUrl(chainId, data.txHash));
      } else {
        const reason = data.reason || 'chain-write-disabled';
        setDemoChainStatus(`Chain: ${reason}`);
      }
      // Immediately refresh history to show the new demo entry
      loadHistory();
    } catch (error) {
      // Network error: fall back to local preset visually
      const preset = DEMO_BACKEND_PRESETS.idle;
      setState(preset.state);
      setLiveData(toWidgetViewModel({ ...preset, updatedAt: new Date().toISOString() }));
      setDataMode('demo-backend');
      setDemoScenario('idle');
      setDemoPrice(priceUsd);
      setDemoChainStatus('Backend unreachable — showing local preset');
      setDemoChainTxUrl('');
    }
    setDemoLoading(false);
  }, [buildExplorerTxUrl, loadHistory]);

  const loadWidgetData = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/widget/latest`);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const payload = await response.json();
      setState(payload.state || 'idle');
      setLiveData(toWidgetViewModel(payload));
      setDataMode('live');
      setLastError('');
    } catch (error) {
      setLiveData(null);
      setDataMode('mock');
      setLastError(error instanceof Error ? error.message : 'Unknown error');
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    const guardedLoad = async () => {
      if (cancelled || demoScenario) {
        return;
      }
      await loadWidgetData();
    };

    guardedLoad();
    const timer = window.setInterval(guardedLoad, 15000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [demoScenario, loadWidgetData]);

  // ── Rule engine handlers ──────────────────────────────────────────────────
  const loadUserRules = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE_URL}/api/widget/user-rules`);
      if (!resp.ok) return;
      const { rules } = await resp.json();
      setSavedRules(rules || '');
      setDraftRules((prev) => (prev === '' && rules ? rules : prev));
    } catch {}
  }, []);

  const handleSaveRules = useCallback(async () => {
    setRulesSaving(true);
    setRulesSaveMsg('');
    try {
      const resp = await fetch(`${API_BASE_URL}/api/widget/user-rules`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rules: draftRules })
      });
      const data = await resp.json();
      if (data.ok) {
        setSavedRules(data.rules || '');
        setRulesSaveMsg('Saved — AI will apply on next 15m candle.');
      } else {
        setRulesSaveMsg('Save failed.');
      }
    } catch {
      setRulesSaveMsg('Save failed (network error).');
    }
    setRulesSaving(false);
    setTimeout(() => setRulesSaveMsg(''), 5000);
  }, [draftRules]);

  // ── History + rules loader effect ────────────────────────────────────────
  // Load rules + history on mount; refresh history every 15 s
  useEffect(() => {
    loadUserRules();
    loadHistory();
    const timer = window.setInterval(loadHistory, 15000);
    return () => window.clearInterval(timer);
  }, [loadUserRules, loadHistory]);

  // ── Derived values ────────────────────────────────────────────────────────
  const stateData = liveData ?? MOCK_FEED[state];
  const isLiveBackendMode = dataMode === 'live' && !demoScenario;

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <main className="flex min-h-screen items-start justify-center px-4 py-10">
      <div className="flex w-full max-w-[1420px] flex-col gap-6 lg:flex-row lg:items-start">

        {/* ── Left info panel ─────────────────────────────────────────────── */}
        <div className="min-w-0 flex-1 rounded-[28px] border border-white/10 bg-slate-950/25 p-6 shadow-2xl backdrop-blur-sm">
          <p className="text-[11px] uppercase tracking-[0.28em] text-slate-400">iPhone Widget Demo</p>
          <h1 className="mt-2 text-2xl font-semibold text-white">On-chain Lucky Cat on Home Screen</h1>
          <p className="mt-2 text-sm text-slate-300">
            Data mode:{' '}
            <span className="font-medium text-white">
              {dataMode === 'live'
                ? 'Live backend feed'
                : dataMode === 'demo-backend'
                  ? 'Demo fake backend'
                  : 'Mock fallback'}
            </span>
          </p>
          {lastError ? <p className="mt-1 text-xs text-rose-200">Backend unavailable: {lastError}</p> : null}

          <div className="mt-5 flex flex-wrap gap-2">
            {DEMO_PRICE_BUTTONS.map((priceValue) => {
              const active = demoPrice === priceValue && !isLiveBackendMode;
              return (
                <button
                  key={priceValue}
                  onClick={async () => {
                    if (isLiveBackendMode || demoLoading) return;
                    await triggerDemo(priceValue);
                  }}
                  disabled={isLiveBackendMode || demoLoading}
                  className={`rounded-lg px-3 py-2 text-sm font-medium transition ${
                    active ? 'bg-white text-slate-900' : 'border border-white/20 bg-white/5 text-white hover:bg-white/10'
                  } ${(isLiveBackendMode || demoLoading) ? 'cursor-not-allowed opacity-45 hover:bg-white/5' : ''}`}
                >
                  {demoLoading && demoPrice === priceValue ? '…' : priceValue}
                </button>
              );
            })}

            <button
              onClick={async () => {
                if (isLiveBackendMode) {
                  // Enter demo mode without triggering a write immediately.
                  setDataMode('demo-backend');
                  setDemoScenario('idle');
                  setDemoPrice(null);
                  setDemoChainStatus('');
                  setDemoChainTxUrl('');
                  setLastError('');
                  return;
                }
                setDemoScenario(null);
                setDemoPrice(null);
                setDemoChainStatus('');
                setDemoChainTxUrl('');
                await loadWidgetData();
              }}
              disabled={demoLoading}
              className={`rounded-lg border border-cyan-300/50 bg-cyan-400/10 px-3 py-2 text-sm font-medium text-cyan-100 transition hover:bg-cyan-300/20 ${demoLoading ? 'opacity-50 cursor-not-allowed' : ''}`}
            >
              {demoLoading ? 'Processing…' : isLiveBackendMode ? 'SWITCH TO DEMO MODE' : 'BACK TO LIVE'}
            </button>
          </div>

          {dataMode === 'demo-backend' && demoChainStatus ? (
            <p className="mt-2 text-xs text-emerald-200">
              {demoChainStatus}
              {demoChainTxUrl ? (
                <>
                  {' '}
                  <a href={demoChainTxUrl} target="_blank" rel="noreferrer"
                    className="underline decoration-emerald-300/80 underline-offset-2 hover:text-emerald-100">
                    View on explorer
                  </a>
                </>
              ) : null}
            </p>
          ) : null}
        </div>

        {/* ── Phone 1: Widget home screen demo ───────────────────────────── */}
        <div className="mx-auto w-[375px] flex-none rounded-[48px] border border-white/20 bg-zinc-900/90 p-2.5 shadow-[0_30px_80px_rgba(0,0,0,0.55)]">
          <div className="relative h-[780px] overflow-hidden rounded-[42px] border border-white/10 bg-gradient-to-br from-slate-800 via-slate-900 to-black">
            <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_25%_20%,rgba(148,163,184,0.25),transparent_35%),radial-gradient(circle_at_80%_80%,rgba(56,189,248,0.22),transparent_42%)]" />
            <div className="absolute left-1/2 top-3.5 h-7 w-36 -translate-x-1/2 rounded-full bg-black/80" />
            <div className="absolute left-5 top-14 text-xs font-medium tracking-wide text-white/80">09:41</div>
            <div className="absolute right-5 top-14 text-[10px] tracking-wide text-white/70">5G  92%</div>
            <div className="absolute left-1/2 top-[112px] w-[349px] -translate-x-1/2 origin-top scale-[0.98]">
              <LuckyCatWidget state={state} data={stateData} catImages={MOCK_CAT_IMAGES} tokenIcons={TOKEN_ICONS} />
            </div>
            <div className="absolute bottom-2 left-1/2 h-1.5 w-32 -translate-x-1/2 rounded-full bg-white/45" />
          </div>
        </div>

        {/* ── Phone 2: AI Rule Engine + History ──────────────────────────── */}
        <div className="mx-auto w-[375px] flex-none rounded-[48px] border border-white/20 bg-zinc-900/90 p-2.5 shadow-[0_30px_80px_rgba(0,0,0,0.55)]">
          <div className="relative h-[780px] overflow-hidden rounded-[42px] border border-white/10 bg-gradient-to-br from-slate-800 via-slate-900 to-black">
            <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_70%_10%,rgba(139,92,246,0.18),transparent_40%),radial-gradient(circle_at_20%_85%,rgba(56,189,248,0.15),transparent_45%)]" />

            {/* Dynamic island */}
            <div className="absolute left-1/2 top-3.5 h-7 w-36 -translate-x-1/2 rounded-full bg-black/80" />

            {/* Status bar */}
            <div className="absolute left-5 top-14 text-xs font-medium tracking-wide text-white/80">09:41</div>
            <div className="absolute right-5 top-14 text-[10px] tracking-wide text-white/70">5G  92%</div>

            {/* App name bar */}
            <div className="absolute left-0 right-0 top-[54px] flex items-center justify-center">
              <span className="text-[13px] font-semibold tracking-tight text-white/90">Lucky Cat</span>
            </div>

            {/* Widget (same state, same data) */}
            <div className="absolute left-1/2 top-[84px] w-[349px] -translate-x-1/2 origin-top scale-[0.92]">
              <LuckyCatWidget state={state} data={stateData} catImages={MOCK_CAT_IMAGES} tokenIcons={TOKEN_ICONS} />
            </div>

            {/* Scrollable content: rule engine + history */}
            {/* Widget at scale 0.92: height = 155 * 0.92 ≈ 143px, positioned at top-84 */}
            {/* Content starts at 84 + 143 + 10 = ~237px */}
            <div className="absolute inset-x-0 top-[240px] bottom-10 overflow-y-auto overscroll-contain px-4 pb-2">

              {/* Rule Engine ──────────────────────────────────────────── */}
              <div className="rounded-2xl border border-white/10 bg-white/5 p-3">
                <div className="mb-2 flex items-center gap-2">
                  <span className="text-[9px] uppercase tracking-[0.22em] text-slate-400">AI Rule Engine</span>
                  {savedRules ? (
                    <span className="rounded-full bg-violet-500/20 px-1.5 py-0.5 text-[9px] font-medium text-violet-300">Active</span>
                  ) : null}
                </div>
                <textarea
                  value={draftRules}
                  onChange={(e) => setDraftRules(e.target.value)}
                  placeholder={"e.g.\n3 consecutive up candles → alpha\n3 consecutive down candles → risk\nprice change > 5% in 24h → alpha"}
                  className="w-full resize-none rounded-xl border border-white/10 bg-black/30 px-3 py-2 text-[12px] leading-relaxed text-white/90 placeholder-white/25 outline-none focus:border-violet-500/40 focus:ring-0"
                  rows={4}
                />
                <div className="mt-2 flex items-center gap-2">
                  <button
                    onClick={handleSaveRules}
                    disabled={rulesSaving}
                    className="rounded-lg border border-violet-400/40 bg-violet-500/20 px-3 py-1.5 text-[12px] font-medium text-violet-200 transition hover:bg-violet-500/30 disabled:opacity-50"
                  >
                    {rulesSaving ? 'Saving…' : 'Save Rules'}
                  </button>
                  {savedRules && !rulesSaveMsg ? (
                    <span className="text-[11px] text-violet-400/80">Rules saved ✓</span>
                  ) : null}
                </div>
                {rulesSaveMsg ? (
                  <p className="mt-1.5 text-[11px] text-emerald-300">{rulesSaveMsg}</p>
                ) : null}
              </div>

              {/* Divider */}
              <div className="my-3 flex items-center gap-2">
                <div className="h-px flex-1 bg-white/10" />
                <span className="text-[9px] uppercase tracking-[0.22em] text-slate-500">History</span>
                <span className="text-[9px] text-slate-600">({historyEntries.length})</span>
                <div className="h-px flex-1 bg-white/10" />
              </div>

              {/* History feed ─────────────────────────────────────────── */}
              {historyEntries.length === 0 ? (
                <p className="text-center text-[11px] text-white/25">No data yet — history updates every 15m candle.</p>
              ) : (
                <div className="space-y-2">
                  {historyEntries.map((entry, i) => {
                    const sc = STATE_COLORS[entry.state] || STATE_COLORS.idle;
                    const changeColor = entry.priceChange24h > 0 ? 'text-emerald-400/75' : entry.priceChange24h < 0 ? 'text-rose-400/75' : 'text-white/35';
                    const sourceLabel = (entry.source || 'live').toLowerCase();
                    const sourceTone = sourceLabel === 'demo' ? 'text-amber-300 bg-amber-500/15 border-amber-500/35' : 'text-cyan-200 bg-cyan-500/15 border-cyan-500/35';
                    const txUrl = entry.txHash ? buildExplorerTxUrl(entry.chainId || 5003, entry.txHash) : '';
                    return (
                      <div key={i} className={`rounded-xl border px-3 py-2 ${sc.badge}`}>
                        <div className="flex items-center justify-between gap-1">
                          <div className="flex items-center gap-1.5">
                            <span className={`rounded-full border px-1.5 py-0.5 text-[9px] font-bold tracking-wider ${sc.badge}`}>
                              {sc.label}
                            </span>
                            <span className={`rounded-full border px-1.5 py-0.5 text-[9px] font-medium tracking-wide ${sourceTone}`}>
                              {sourceLabel}
                            </span>
                          </div>
                          <span className="text-[10px] text-white/35">{formatTime(entry.timestamp)}</span>
                        </div>
                        <p className="mt-1.5 line-clamp-2 text-[11px] leading-snug text-white/80">{entry.message}</p>
                        {entry.txHash ? (
                          <p className="mt-1 text-[10px] text-emerald-200/90">
                            On-chain tx: {entry.txHash.slice(0, 10)}...{entry.txHash.slice(-8)}
                            {txUrl ? (
                              <>
                                {' '}
                                <a
                                  href={txUrl}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="underline decoration-emerald-300/80 underline-offset-2 hover:text-emerald-100"
                                >
                                  View on explorer
                                </a>
                              </>
                            ) : null}
                          </p>
                        ) : null}
                        <div className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 text-[10px]">
                          {entry.priceUsd > 0 && (
                            <span className="text-white/55">${entry.priceUsd.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 })}</span>
                          )}
                          {entry.priceChange24h !== 0 && (
                            <span className={changeColor}>
                              {entry.priceChange24h > 0 ? '+' : ''}{entry.priceChange24h.toFixed(2)}%
                            </span>
                          )}
                          {(entry.buysM5 > 0 || entry.sellsM5 > 0) && (
                            <span className="text-white/35">B/S {entry.buysM5}/{entry.sellsM5}</span>
                          )}
                          {entry.volM5 > 0 && (
                            <span className="text-white/35">Vol ${entry.volM5.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Home indicator */}
            <div className="absolute bottom-2 left-1/2 h-1.5 w-32 -translate-x-1/2 rounded-full bg-white/45" />
          </div>
        </div>

      </div>
    </main>
  );
}

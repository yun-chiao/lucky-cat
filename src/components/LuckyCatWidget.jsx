import { AnimatePresence, motion } from 'framer-motion';

const STATE_CONFIG = {
  idle: {
    emotion: "I'm watching mETH for you...",
    heroMetric: '$200',
    token: 'mETH',
    updatedAt: '10s ago',
    glow: 'rgba(96,165,250,0.55)',
    ring: 'from-slate-400/45 to-sky-400/70',
    catLabel: 'IDLE CAT',
    panelTone: 'border-slate-300/20 bg-slate-900/45',
    metricTone: 'border-slate-300/30 bg-slate-300/10 text-slate-100',
    contextTone: 'text-slate-300',
    alertOverlay: 'from-sky-500/10 via-transparent to-transparent'
  },
  alpha: {
    emotion: 'Momentum detected on mETH.',
    heroMetric: '$200',
    token: 'USDY',
    updatedAt: '6s ago',
    glow: 'rgba(250,204,21,0.62)',
    ring: 'from-amber-300/50 to-yellow-400/85',
    catLabel: 'ALPHA CAT',
    panelTone: 'border-amber-200/30 bg-amber-500/8',
    metricTone: 'border-amber-200/60 bg-amber-400/15 text-amber-100',
    contextTone: 'text-amber-100',
    alertOverlay: 'from-amber-400/18 via-transparent to-transparent'
  },
  risk: {
    emotion: 'Heavy sell pressure detected on mETH.',
    heroMetric: '$200',
    token: 'mETH',
    updatedAt: '4s ago',
    glow: 'rgba(248,113,113,0.66)',
    ring: 'from-rose-400/45 to-red-500/88',
    catLabel: 'RISK CAT',
    panelTone: 'border-rose-200/30 bg-rose-500/8',
    metricTone: 'border-rose-200/55 bg-rose-400/15 text-rose-100',
    contextTone: 'text-rose-100',
    alertOverlay: 'from-red-500/20 via-transparent to-transparent'
  }
};

function TokenBadge({ token, tokenIcon }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-black/25 px-2 py-0.5">
      {tokenIcon ? (
        <img src={tokenIcon} alt={`${token} logo`} className="relative -top-[0.5px] h-3.5 w-3.5 rounded-full object-cover" />
      ) : (
        <span className="h-3.5 w-3.5 rounded-full bg-white/40" />
      )}
      <span className="relative top-[0.5px] leading-none">${token}</span>
    </span>
  );
}

function LuckyCatVisual({ state, config }) {
  const catImage = config.image;

  return (
    <div className="relative flex h-full w-full items-center justify-center overflow-hidden rounded-[20px] bg-slate-950/65">
      <motion.div
        className={`absolute inset-5 rounded-full bg-gradient-to-br ${config.ring} blur-xl`}
        animate={{ scale: state === 'idle' ? [1, 1.03, 1] : [1, 1.08, 1] }}
        transition={{ duration: 2.2, repeat: Infinity, ease: 'easeInOut' }}
      />
      <AnimatePresence mode="wait">
        <motion.div
          key={state}
          initial={{ opacity: 0, y: 10, scale: 0.92 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: -8, scale: 0.94 }}
          transition={{ duration: 0.26 }}
          className={`relative z-10 flex h-[112px] w-[112px] items-center justify-center overflow-hidden rounded-[22px] ${
            catImage
              ? 'border border-transparent bg-transparent p-0'
              : 'border border-white/20 bg-gradient-to-br from-slate-100/20 to-slate-200/5 p-1.5'
          }`}
        >
          {catImage ? (
            <img
              src={catImage}
              alt={`${state} lucky cat`}
              className="h-full w-full scale-[1.16] object-contain"
            />
          ) : (
            <div className="text-center text-[10px] font-semibold tracking-[0.24em] text-white/90">
              =^.^=
              <div className="mt-1 text-[8px] text-white/70">{config.catLabel}</div>
            </div>
          )}
        </motion.div>
      </AnimatePresence>
      <motion.div
        className="absolute -bottom-6 h-12 w-24 rounded-full blur-2xl"
        style={{ backgroundColor: config.glow }}
        animate={{ opacity: [0.48, 0.9, 0.48], scaleX: [0.92, 1.2, 0.92] }}
        transition={{ duration: 1.8, repeat: Infinity, ease: 'easeInOut' }}
      />

      {state === 'risk' ? (
        <motion.div
          className="pointer-events-none absolute inset-0 bg-gradient-to-br from-red-500/18 via-transparent to-transparent"
          animate={{ opacity: [0.15, 0.5, 0.15] }}
          transition={{ duration: 1.2, repeat: Infinity, ease: 'easeInOut' }}
        />
      ) : null}
    </div>
  );
}

export default function LuckyCatWidget({ state = 'idle', data, catImages = {}, tokenIcons = {} }) {
  const config = {
    ...(STATE_CONFIG[state] || STATE_CONFIG.idle),
    image: catImages?.[state] || ''
  };

  const tokenKey = (data?.token || config.token || '').toLowerCase();
  const mergedData = {
    emotion: data?.emotion ?? config.emotion,
    heroMetric: data?.heroMetric ?? config.heroMetric,
    token: data?.token ?? config.token,
    updatedAt: data?.updatedAt ?? config.updatedAt,
    tokenIcon: data?.tokenIcon ?? tokenIcons?.[tokenKey] ?? ''
  };

  return (
    <motion.section
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: 'easeOut' }}
      className="relative h-[155px] w-[329px] overflow-hidden rounded-[24px] border border-white/15 bg-slate-900/55 p-[10px] text-white shadow-[0_8px_40px_rgba(6,10,30,0.42)] backdrop-blur-xl"
      aria-label="On-chain Lucky Cat Widget"
    >
      <div className="pointer-events-none absolute -top-20 right-[-20%] h-44 w-44 rounded-full bg-cyan-400/10 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-20 left-[-12%] h-40 w-40 rounded-full bg-amber-300/10 blur-3xl" />
      <div className={`pointer-events-none absolute inset-0 bg-gradient-to-br ${config.alertOverlay}`} />

      <div className="relative grid h-full grid-cols-[43%_57%] gap-[8px]">
        <div className="h-full min-w-0">
          <LuckyCatVisual state={state} config={config} />
        </div>

        <div className={`flex h-full min-w-0 flex-col justify-between rounded-2xl  px-2.5 py-2`}>
          <header className="min-w-0">
            <p className="text-[8px] mb-2 uppercase tracking-[0.18em] text-slate-400">Emotion</p>
            <h3 className="mt-1 line-clamp-2 text-[14px] font-medium leading-[1.28] text-white">{mergedData.emotion}</h3>
          </header>

          <div
            className={`inline-flex w-full max-w-full items-center gap-1.5 rounded-lg border px-2 py-1.5 text-[10px] font-semibold leading-tight ${config.metricTone}`}
            style={{ fontFamily: '"Roboto Mono", ui-monospace, SFMono-Regular, Menlo, monospace' }}
          >
            <TokenBadge token={mergedData.token} tokenIcon={mergedData.tokenIcon} />
            <span className="truncate leading-none relative top-[0.5px]">{mergedData.heroMetric}</span>
          </div>

          <footer className={`text-[10px] ${config.contextTone}`}>
            ⏱️ Update: {mergedData.updatedAt}
          </footer>
        </div>
      </div>
    </motion.section>
  );
}

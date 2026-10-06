import { STAGES } from '../data/constants'

/**
 * The visual centre of the application: a glowing core whose state reflects
 * what the agent is doing right now.
 *
 * Deliberately not a chart. Rings are drawn as SVG so the glow scales cleanly,
 * and the only continuous motion is a slow orbital sweep plus a core pulse that
 * runs while a scenario is executing. Both stop when the agent is idle, and
 * both are suppressed entirely under prefers-reduced-motion.
 */
export default function AgentCore({ stage, running, status, lastAction }) {
  const active = STAGES.find((s) => s.key === stage)
  const headline = running ? active?.label ?? 'OBSERVING' : 'STANDING BY'
  const sub = running
    ? active?.blurb ?? 'watching telemetry'
    : lastAction
      ? `last action: ${lastAction}`
      : 'no incident open'

  return (
    <div className="relative flex flex-col items-center">
      <div className="relative h-56 w-56 sm:h-64 sm:w-64">
        {/* Ambient glow */}
        <div
          className={`absolute inset-6 rounded-full blur-2xl transition-opacity duration-700 ${
            running ? 'bg-agent/30 opacity-100' : 'bg-agent/15 opacity-70'
          }`}
          aria-hidden
        />

        <svg viewBox="0 0 200 200" className="relative h-full w-full" role="img"
             aria-label={`Autonomous recovery agent, ${headline}`}>
          <defs>
            <radialGradient id="coreFill" cx="50%" cy="42%" r="58%">
              <stop offset="0%" stopColor="#CFFAFE" stopOpacity="0.95" />
              <stop offset="45%" stopColor="#22D3EE" stopOpacity="0.55" />
              <stop offset="100%" stopColor="#0E7490" stopOpacity="0.12" />
            </radialGradient>
            <linearGradient id="ringStroke" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stopColor="#22D3EE" stopOpacity="0.85" />
              <stop offset="60%" stopColor="#8B5CF6" stopOpacity="0.55" />
              <stop offset="100%" stopColor="#22D3EE" stopOpacity="0.15" />
            </linearGradient>
          </defs>

          {/* Static lattice rings */}
          <circle cx="100" cy="100" r="86" fill="none" stroke="rgba(148,163,184,0.16)" strokeWidth="0.75" />
          <circle cx="100" cy="100" r="68" fill="none" stroke="rgba(148,163,184,0.10)" strokeWidth="0.75" />

          {/* Orbital sweep — the only ambient motion */}
          <g className={running ? 'origin-center animate-sweep' : 'origin-center'}>
            <circle
              cx="100" cy="100" r="86" fill="none"
              stroke="url(#ringStroke)" strokeWidth="1.6"
              strokeLinecap="round" strokeDasharray="52 220"
            />
            <circle cx="186" cy="100" r="2.6" fill="#22D3EE" />
          </g>

          {/* Segmented progress ring: one tick per pipeline stage */}
          {STAGES.map((s, i) => {
            const total = STAGES.length
            const angle = (i / total) * 2 * Math.PI - Math.PI / 2
            const r = 76
            const x = 100 + r * Math.cos(angle)
            const y = 100 + r * Math.sin(angle)
            const isActive = s.key === stage
            const isPast = stage && STAGES.findIndex((z) => z.key === stage) > i
            return (
              <circle
                key={s.key}
                cx={x} cy={y}
                r={isActive ? 4.2 : 2.4}
                fill={isActive ? '#22D3EE' : isPast ? '#34D399' : 'rgba(148,163,184,0.35)'}
                className={isActive ? 'animate-corepulse' : ''}
              />
            )
          })}

          {/* Core */}
          <circle
            cx="100" cy="100" r="46"
            fill="url(#coreFill)"
            className={running ? 'origin-center animate-corepulse' : ''}
          />
          <circle cx="100" cy="100" r="46" fill="none" stroke="rgba(207,250,254,0.45)" strokeWidth="1" />
          <circle
            cx="100" cy="100" r="30" fill="none"
            stroke="rgba(207,250,254,0.30)" strokeWidth="0.8"
            strokeDasharray="4 6"
            className={running ? 'animate-drift' : ''}
          />
        </svg>

        {/* Core readout */}
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center">
          <span className="num text-[10px] tracking-[0.18em] text-cyan-100/70">
            {running ? 'ACTIVE' : 'IDLE'}
          </span>
          <span className="mt-1 text-lg font-semibold text-white">{headline}</span>
        </div>
      </div>

      <p className="mt-3 max-w-[28ch] text-center text-[13px] leading-relaxed text-slate-400">{sub}</p>
      {status && (
        <p className="num mt-1 text-[11px] text-slate-500">{status}</p>
      )}
    </div>
  )
}

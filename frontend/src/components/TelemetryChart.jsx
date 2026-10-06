import {
  Area,
  AreaChart,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

const AXIS = { stroke: 'rgba(148,163,184,0.35)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }

function ChartTooltip({ active, payload, label, unit }) {
  if (!active || !payload?.length) return null
  return (
    <div className="glass px-3 py-2 text-[11px]">
      <p className="num text-slate-400">sample {label}</p>
      {payload.map((p) => (
        <p key={p.dataKey} className="num mt-0.5 text-slate-100">
          <span className="mr-2 inline-block h-1.5 w-1.5 rounded-full align-middle" style={{ background: p.color }} />
          {/* A range series arrives as [low, high], so it is not a number. */}
          {p.name}:{' '}
          {Array.isArray(p.value)
            ? p.value.map((v) => (typeof v === 'number' ? v.toFixed(2) : v)).join(' to ')
            : typeof p.value === 'number'
              ? p.value.toFixed(3)
              : p.value}
          {unit ? ` ${unit}` : ''}
        </p>
      ))}
    </div>
  )
}

/**
 * The anomaly-score trace: g_t against the chi-square threshold.
 *
 * This is the single most informative telemetry view in the system, because the
 * threshold line is exactly the decision boundary MONITOR uses to open an
 * incident — crossings are visible rather than inferred. `markerIndex` draws a
 * vertical line at the sample that opened a specific incident.
 */
export function AnomalyChart({ samples = [], height = 190, markerIndex = null, windowed = false }) {
  const view = windowed ? samples.slice(-dynamicWindow(samples)) : samples
  const offset = samples.length - view.length
  const data = view.map((s, i) => ({
    i: i + offset,
    score: s.score,
    threshold: s.threshold,
    triggered: s.triggered,
  }))
  const threshold = samples.at(-1)?.threshold ?? null

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 4, right: 6, bottom: 0, left: -18 }}>
        <defs>
          <linearGradient id="scoreFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#22D3EE" stopOpacity={0.45} />
            <stop offset="100%" stopColor="#22D3EE" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="rgba(148,163,184,0.09)" vertical={false} />
        <XAxis dataKey="i" tick={AXIS} tickLine={false} axisLine={false} minTickGap={36} />
        <YAxis tick={AXIS} tickLine={false} axisLine={false} width={44} />
        <Tooltip content={<ChartTooltip />} cursor={{ stroke: 'rgba(148,163,184,0.3)' }} />
        {threshold !== null && (
          <ReferenceLine
            y={threshold}
            stroke="#FB7185"
            strokeDasharray="4 4"
            label={{
              value: `chi2 = ${threshold.toFixed(1)}`,
              fill: '#FB7185',
              fontSize: 10,
              position: 'insideTopRight',
              fontFamily: 'JetBrains Mono, monospace',
            }}
          />
        )}
        {markerIndex !== null && markerIndex !== undefined && (
          <ReferenceLine
            x={markerIndex}
            stroke="#FBBF24"
            strokeDasharray="2 3"
            label={{
              value: 'incident opened',
              fill: '#FBBF24',
              fontSize: 10,
              position: 'insideTopLeft',
              fontFamily: 'JetBrains Mono, monospace',
            }}
          />
        )}
        <Area
          type="monotone"
          dataKey="score"
          name="g_t"
          stroke="#22D3EE"
          strokeWidth={1.6}
          fill="url(#scoreFill)"
          isAnimationActive={false}
          dot={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}

/**
 * How many of the most recent samples a live chart should show.
 *
 * Not a fixed window. A long window is right while nothing is happening —
 * it shows the baseline holding steady, which is the thing worth seeing —
 * but it is the wrong view the moment an excursion starts, because the
 * detail that matters gets compressed into a few pixels at the right-hand
 * edge. So the window contracts geometrically toward `min` as the worst
 * channel moves away from its baseline, and relaxes back toward `max` when
 * it settles.
 *
 * The contraction is exponential in the deviation, which is what makes the
 * plot move continuously rather than snapping between two fixed widths: a
 * sample at two sigma barely narrows it, one at six sigma pulls it most of
 * the way in.
 *
 * Driven by `deviation_sigma`, the detector's own measure of how far the
 * device sits outside its normal range — so the view follows the same
 * quantity the severity verdict does, rather than a cosmetic heuristic.
 */
export function dynamicWindow(samples, { min = 60, max = 240, k = 0.35 } = {}) {
  if (!samples.length) return max
  const recent = samples.slice(-12)
  const excursion = Math.max(
    0,
    ...recent.map((s) => s.deviation_sigma ?? Math.abs(s.score ?? 0) / (s.threshold || 1)),
  )
  const span = max - min
  const width = min + span * Math.exp(-k * excursion)
  return Math.max(min, Math.min(max, Math.round(width)))
}

/**
 * One telemetry channel over time — the five real features, never CPU/RAM.
 *
 * `band` overlays the detector's EWMA baseline and its ±`bandSigma` envelope.
 * Both come from the detector's own state at each sample (MonitorReading's
 * `baseline` and `band`), not from a line refitted in the browser, so what
 * the chart shows is literally what the sample was judged against: inside
 * the envelope is nominal, outside it is what moves the score.
 */
export function FeatureChart({
  samples = [],
  featureKey,
  color = '#22D3EE',
  height = 120,
  unit,
  band = false,
  bandSigma = 3,
  windowed = false,
}) {
  const view = windowed ? samples.slice(-dynamicWindow(samples)) : samples
  const offset = samples.length - view.length
  const data = view.map((s, i) => {
    const mu = s.baseline?.[featureKey]
    const sd = s.band?.[featureKey]
    const row = { i: i + offset, v: s.features?.[featureKey] }
    if (band && typeof mu === 'number' && typeof sd === 'number') {
      row.mu = mu
      // A range Area, given as [low, high]. Not two stacked Areas: Recharts
      // accumulates negative and positive values into separate stacks, so a
      // band around RSRP - which sits near -90 dBm - would be drawn from
      // zero in both directions instead of around the baseline.
      row.range = [mu - bandSigma * sd, mu + bandSigma * sd]
    }
    return row
  })
  const gradId = `band-${featureKey}`

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 6, right: 6, bottom: 0, left: -20 }}>
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.16} />
            <stop offset="100%" stopColor={color} stopOpacity={0.16} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="rgba(148,163,184,0.07)" vertical={false} />
        <XAxis dataKey="i" tick={AXIS} tickLine={false} axisLine={false} minTickGap={40} />
        <YAxis tick={AXIS} tickLine={false} axisLine={false} width={46} domain={['auto', 'auto']} />
        <Tooltip content={<ChartTooltip unit={unit} />} cursor={{ stroke: 'rgba(148,163,184,0.25)' }} />
        {band && (
          <Area
            type="monotone"
            dataKey="range"
            name={`EWMA ±${bandSigma}σ`}
            stroke="none"
            fill={`url(#${gradId})`}
            isAnimationActive={false}
            tooltipType="none"
          />
        )}
        {band && (
          <Line
            type="monotone"
            dataKey="mu"
            name="EWMA baseline"
            stroke={color}
            strokeOpacity={0.55}
            strokeWidth={1}
            strokeDasharray="3 3"
            dot={false}
            isAnimationActive={false}
          />
        )}
        <Line
          type="monotone"
          dataKey="v"
          name={featureKey}
          stroke={color}
          strokeWidth={1.5}
          dot={false}
          isAnimationActive={false}
        />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

/** Compact sparkline for the Overview column. */
export function MiniTelemetry({ samples = [], featureKey = 'score', height = 54, color = '#22D3EE' }) {
  const data = samples.map((s, i) => ({
    i,
    v: featureKey === 'score' ? s.score : s.features?.[featureKey],
  }))
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id={`mini-${featureKey}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.5} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Area
          type="monotone"
          dataKey="v"
          stroke={color}
          strokeWidth={1.3}
          fill={`url(#mini-${featureKey})`}
          dot={false}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}

import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { GraduationCap, Scale } from 'lucide-react'
import LearningCard from '../components/LearningCard'
import { Field, Notice, Panel, Spinner } from '../components/Primitives'
import { fmt } from '../data/constants'

const AXIS = { stroke: 'rgba(148,163,184,0.35)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }

/**
 * PPO-Lagrangian. Only what learn.py actually computes is shown: the update
 * history (reward, cost estimates, multipliers, entropy, violation rate), the
 * current greedy action distribution, and the evaluation of that policy on the
 * surrogate environment the module ships.
 */
export default function Learning({ learning, loading, error }) {
  if (loading) return <Spinner label="Loading policy state" />
  if (error) {
    return <Notice tone="block" title="Learning data is unavailable">The backend is not responding.</Notice>
  }

  if (!learning?.enabled) {
    return (
      <Notice title="Learning is switched off for this session">
        The orchestrator was built without a PPO-Lagrangian learner, so no policy updates are
        recorded. Reset the session with learning enabled to populate this screen.
      </Notice>
    )
  }

  const history = learning.history ?? []
  const latest = learning.latest
  const d = learning.d_limits ?? []

  const chart = history.map((h) => ({
    i: h.iteration,
    reward: h.mean_reward,
    entropy: h.entropy,
    c1: h.cost_estimates?.[0],
    c2: h.cost_estimates?.[1],
    lam1: h.lambdas?.[0],
    lam2: h.lambdas?.[1],
  }))

  if (!history.length) {
    return (
      <div className="space-y-5">
        <Notice title="The policy has not updated yet">
          Transitions are batched: the learner updates once {learning.update_every} incidents have
          been written to the replay buffer. It currently holds {learning.buffer_size}. Run more
          scenarios to trigger an update.
        </Notice>
        <ActionMix learning={learning} />
      </div>
    )
  }

  return (
    <div className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <LearningCard
          label="Mean reward J_R"
          value={fmt.num(latest?.mean_reward, 3)}
          hint="averaged over the last update batch"
        />
        <LearningCard
          label="Violation rate"
          value={fmt.pct(latest?.violation_rate, 1)}
          meter={{ value: latest?.violation_rate ?? 0, max: 1, limit: d[0] }}
          tone={(latest?.violation_rate ?? 0) > (d[0] ?? 1) ? 'block' : 'pass'}
          hint={`constraint bound d₁ = ${fmt.num(d[0], 3)}`}
        />
        <LearningCard
          label="Policy entropy H"
          value={fmt.num(latest?.entropy, 3)}
          hint="higher means the policy has not collapsed onto one action"
        />
        <LearningCard
          label="Updates"
          value={learning.updates}
          hint={`batch cadence ${learning.update_every}, buffer holds ${learning.buffer_size}`}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Reward and constraint cost" icon={GraduationCap} meta="per update">
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={chart} margin={{ top: 6, right: 8, bottom: 0, left: -18 }}>
              <CartesianGrid stroke="rgba(148,163,184,0.08)" vertical={false} />
              <XAxis dataKey="i" tick={AXIS} tickLine={false} axisLine={false} />
              <YAxis tick={AXIS} tickLine={false} axisLine={false} width={46} />
              <Tooltip contentStyle={{ background: '#0B1020', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 12, fontSize: 11 }} />
              {d[0] !== undefined && <ReferenceLine y={d[0]} stroke="#FB7185" strokeDasharray="4 4" />}
              <Line type="monotone" dataKey="reward" name="J_R" stroke="#22D3EE" strokeWidth={1.6} dot={false} />
              <Line type="monotone" dataKey="c1" name="J_C1" stroke="#FB7185" strokeWidth={1.6} dot={false} />
              <Line type="monotone" dataKey="c2" name="J_C2" stroke="#FBBF24" strokeWidth={1.4} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </Panel>

        <Panel title="Dual variables λ" icon={Scale} meta="dual ascent on the multipliers">
          <p className="mb-2 max-w-[62ch] text-[12px] leading-relaxed text-slate-400">
            λ rises while a constraint binds and decays once the policy stops violating it, so the
            peak — not the current value — is what the PLAN weights are rescaled against.
          </p>
          <ResponsiveContainer width="100%" height={188}>
            <LineChart data={chart} margin={{ top: 6, right: 8, bottom: 0, left: -18 }}>
              <CartesianGrid stroke="rgba(148,163,184,0.08)" vertical={false} />
              <XAxis dataKey="i" tick={AXIS} tickLine={false} axisLine={false} />
              <YAxis tick={AXIS} tickLine={false} axisLine={false} width={46} />
              <Tooltip contentStyle={{ background: '#0B1020', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 12, fontSize: 11 }} />
              <Line type="monotone" dataKey="lam1" name="λ₁" stroke="#8B5CF6" strokeWidth={1.6} dot={false} />
              <Line type="monotone" dataKey="lam2" name="λ₂" stroke="#F472B6" strokeWidth={1.4} dot={false} />
            </LineChart>
          </ResponsiveContainer>
          <div className="mt-2 grid grid-cols-2 gap-x-6 border-t border-white/[0.07] pt-2">
            <div className="divide-y divide-white/[0.05]">
              <Field label="λ current" value={(learning.lambdas ?? []).map((v) => fmt.num(v, 3)).join(', ')} />
              <Field label="λ peak" value={(learning.peak_lambdas ?? []).map((v) => fmt.num(v, 3)).join(', ')} />
            </div>
            <div className="divide-y divide-white/[0.05]">
              <Field label="PLAN weights (base)" value={(learning.plan_weights?.base ?? []).map((v) => fmt.num(v)).join(', ')} />
              <Field label="PLAN weights (now)" value={(learning.plan_weights?.current ?? []).map((v) => fmt.num(v)).join(', ')} />
            </div>
          </div>
        </Panel>
      </div>

      <ActionMix learning={learning} />
    </div>
  )
}

function ActionMix({ learning }) {
  const mix = (learning.action_distribution ?? []).map((a) => ({ name: a.label, p: a.p }))
  const evalv = learning.evaluation ?? {}
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
      <Panel title="Greedy action distribution" meta="current policy over the belief simplex">
        <ResponsiveContainer width="100%" height={210}>
          <BarChart data={mix} margin={{ top: 6, right: 8, bottom: 0, left: -18 }}>
            <CartesianGrid stroke="rgba(148,163,184,0.08)" vertical={false} />
            <XAxis dataKey="name" tick={{ ...AXIS, fontSize: 9 }} tickLine={false} axisLine={false} interval={0} />
            <YAxis tick={AXIS} tickLine={false} axisLine={false} width={46} />
            <Tooltip cursor={{ fill: 'rgba(255,255,255,0.04)' }} contentStyle={{ background: '#0B1020', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 12, fontSize: 11 }} />
            <Bar dataKey="p" name="probability" fill="#8B5CF6" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </Panel>

      <Panel title="Policy evaluation" meta="on the surrogate environment">
        <div className="divide-y divide-white/[0.05]">
          <Field label="Mean reward" value={fmt.num(evalv.mean_reward, 3)} />
          <Field label="Resolution rate" value={fmt.pct(evalv.resolution_rate, 1)} />
          <Field
            label="Violation rate"
            value={fmt.pct(evalv.violation_rate, 1)}
            tone={(evalv.violation_rate ?? 0) > 0 ? 'text-hold' : 'text-pass'}
          />
          <Field label="Mean blast radius" value={fmt.num(evalv.mean_blast_radius, 2)} />
        </div>
        <p className="mt-3 text-[11.5px] leading-relaxed text-slate-500">
          A constrained learner should find the best policy inside the same envelope ACT enforces,
          rather than converging on the widest-blast-radius action that fixes everything.
        </p>
      </Panel>
    </div>
  )
}

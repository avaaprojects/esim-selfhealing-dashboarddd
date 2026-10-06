import { Fragment, useState } from 'react'
import {
  Activity,
  Brain,
  CircuitBoard,
  FlaskConical,
  Gauge,
  GraduationCap,
  LayoutDashboard,
  LogOut,
  Menu,
  Play,
  Radio,
  ShieldCheck,
  Database,
  Cpu,
  MessageSquare,
  SlidersHorizontal,
  Workflow,
  X,
} from 'lucide-react'
import { Badge } from '../components/Primitives'

/**
 * The sidebar follows the flow the system actually runs:
 *
 *   INPUT -> OUTPUT -> MONITORING -> SELF-HEALING -> REPORTS
 *
 * `group` starts a labelled section, and the sections are the same for every
 * role — a client simply sees fewer items inside them, rather than a
 * differently organised menu.
 */
export const NAV = [
  // INPUT: what enters the system. Both roles; a client works on their own company's record.
  { key: 'input', label: 'Input / configuration', icon: SlidersHorizontal, group: 'Input' },
  // OUTPUT: what the system detected, decided and did. Full screen, opened by Process data.
  { key: 'output', label: 'Self-healing output', icon: Workflow, group: 'Output' },
  { key: 'overview', label: 'Overview', icon: LayoutDashboard, group: 'Monitoring' },
  { key: 'realtime', label: 'Real-time data', icon: Radio },
  { key: 'monitor', label: 'Live monitor', icon: Gauge },
  { key: 'devices', label: 'Devices', icon: Cpu },
  { key: 'incidents', label: 'Incidents', icon: Activity, group: 'Self-healing' },
  { key: 'reasoning', label: 'Agent reasoning', icon: Brain },
  { key: 'actions', label: 'Actions', icon: CircuitBoard },
  { key: 'safety', label: 'Safety', icon: ShieldCheck },
  { key: 'memory', label: 'Memory', icon: Database },
  { key: 'learning', label: 'Learning', icon: GraduationCap },
  { key: 'simulation', label: 'Simulation', icon: FlaskConical },
  // The client's own history is "My reports"; the owner manages everyone's.
  { key: 'reports', label: 'My reports', ownerLabel: 'Reports', icon: MessageSquare, group: 'Reports' },
]

const labelFor = (item, isOwner) => (isOwner && item.ownerLabel) || item.label

export default function DashboardLayout({
  page,
  onNavigate,
  onRun,
  running,
  counters,
  children,
  role,
  username,
  onLogout,
  reportSummary,
}) {
  const [menuOpen, setMenuOpen] = useState(false)
  const isOwner = role === 'owner'
  // Owner: reports still waiting to be acknowledged. Client: their open ones.
  const badgeCount = isOwner ? reportSummary?.sent ?? 0 : reportSummary?.open ?? 0

  const nav = (
    <nav className="space-y-1" aria-label="Sections">
      {NAV.filter((item) => !item.ownerOnly || isOwner).map((item) => {
        const Icon = item.icon
        const active = page === item.key
        return (
          <Fragment key={item.key}>
            {item.group && (
              <p className="px-3 pb-1 pt-3 text-[11px] font-medium text-slate-600">{item.group}</p>
            )}
          <button
            type="button"
            onClick={() => {
              onNavigate(item.key)
              setMenuOpen(false)
            }}
            aria-current={active ? 'page' : undefined}
            className={[
              'flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-[13px] transition-colors',
              active
                ? 'bg-agent/10 text-agent ring-1 ring-agent/25'
                : 'text-slate-400 hover:bg-white/[0.04] hover:text-slate-200',
            ].join(' ')}
          >
            <Icon size={15} aria-hidden />
            <span className="flex-1 text-left">{labelFor(item, isOwner)}</span>
            {item.key === 'reports' && badgeCount > 0 && (
              <span
                className="num rounded-full bg-hold/15 px-1.5 py-0.5 text-[10.5px] text-hold ring-1 ring-hold/30"
                title={isOwner ? 'Reports waiting for you' : 'Reports still open'}
              >
                {badgeCount}
              </span>
            )}
          </button>
          </Fragment>
        )
      })}
    </nav>
  )

  const runButton = (
    <button
      type="button"
      onClick={onRun}
      disabled={running}
      className={[
        'group flex w-full items-center justify-center gap-2.5 rounded-xl px-4 py-3 text-[13px] font-semibold transition-all',
        running
          ? 'cursor-wait bg-agent/15 text-agent/70'
          : 'bg-gradient-to-r from-agent to-cyan-300 text-slate-950 shadow-glow hover:brightness-110 active:scale-[0.99]',
      ].join(' ')}
    >
      <Play size={14} className={running ? 'animate-corepulse' : ''} aria-hidden />
      {running ? 'Agent working…' : 'Run self-healing'}
    </button>
  )

  return (
    <div className="min-h-screen lg:flex">
      {/* Sidebar — persistent on desktop, sheet on small screens */}
      <aside
        className={[
          'fixed inset-y-0 left-0 z-40 w-[248px] shrink-0 border-r border-white/[0.07] bg-abyss/85 px-4 py-5 backdrop-blur-xl transition-transform lg:static lg:translate-x-0',
          menuOpen ? 'translate-x-0' : '-translate-x-full',
        ].join(' ')}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <span className="grid h-8 w-8 place-items-center rounded-xl bg-agent/15 text-agent ring-1 ring-agent/30">
              <Gauge size={16} aria-hidden />
            </span>
            <div className="leading-tight">
              <p className="text-[13px] font-semibold text-slate-100">Recovery Agent</p>
              <p className="text-[10.5px] text-slate-500">eSIM / eUICC estate</p>
            </div>
          </div>
          <button
            type="button"
            className="rounded-lg p-1.5 text-slate-400 hover:bg-white/5 lg:hidden"
            onClick={() => setMenuOpen(false)}
            aria-label="Close menu"
          >
            <X size={16} />
          </button>
        </div>

        <div className="mt-6">{nav}</div>

        <div className="mt-6 space-y-3">
          {runButton}
          <div className="glass-quiet px-3 py-2.5">
            <dl className="space-y-1.5 text-[11px]">
              <Row label="Incidents" value={counters?.incidents_total ?? 0} />
              <Row label="Auto-remediated" value={counters?.auto_remediated ?? 0} />
              <Row label="Awaiting operator" value={counters?.human_in_loop ?? 0} />
              <Row label="Escalated" value={counters?.escalations ?? 0} />
              <Row label="Memory records" value={counters?.memory_records ?? 0} />
            </dl>
          </div>
        </div>

        <div className="mt-6 flex items-center justify-between gap-2 border-t border-white/[0.07] pt-4">
          <div className="min-w-0">
            <p className="truncate text-[11.5px] font-medium text-slate-300">{username}</p>
            <p className="text-[10.5px] uppercase tracking-wide text-slate-600">
              {isOwner ? 'Owner / Administrator' : 'Client / Viewer'}
            </p>
          </div>
          <button
            type="button"
            onClick={onLogout}
            className="shrink-0 rounded-lg p-1.5 text-slate-500 hover:bg-white/5 hover:text-slate-300"
            aria-label="Sign out"
            title="Sign out"
          >
            <LogOut size={14} />
          </button>
        </div>

        <p className="mt-4 text-[10.5px] leading-relaxed text-slate-600">
          Simulation and real-time telemetry run against a bundled RSP model. No connection is
          made to live telecom infrastructure.
        </p>
      </aside>

      {menuOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/60 lg:hidden"
          onClick={() => setMenuOpen(false)}
          aria-hidden
        />
      )}

      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-20 flex items-center gap-3 border-b border-white/[0.07] bg-void/75 px-4 py-3 backdrop-blur-xl sm:px-6">
          <button
            type="button"
            className="rounded-lg p-2 text-slate-300 hover:bg-white/5 lg:hidden"
            onClick={() => setMenuOpen(true)}
            aria-label="Open menu"
          >
            <Menu size={17} />
          </button>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-[15px] font-semibold text-slate-100">
              {(() => {
                const item = NAV.find((n) => n.key === page)
                return item ? labelFor(item, isOwner) : 'Overview'
              })()}
            </h1>
          </div>
          <Badge tone={isOwner ? 'agent' : 'policy'}>{isOwner ? 'Owner' : 'Client'}</Badge>
          {isOwner && <div className="hidden w-[196px] sm:block">{runButton}</div>}
        </header>

        <main className="px-4 py-5 sm:px-6 sm:py-6">{children}</main>
      </div>
    </div>
  )
}

function Row({ label, value }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-slate-500">{label}</dt>
      <dd className="num text-slate-200">{value}</dd>
    </div>
  )
}

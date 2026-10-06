import { Activity, CircleAlert, PlayCircle, Sparkles } from 'lucide-react'
import { fmt } from '../data/constants'

const ICON = {
  run: PlayCircle,
  incident: CircleAlert,
  session: Sparkles,
}

/** Recent agent activity, newest first. Source: the session's own event log. */
export default function ActivityFeed({ items = [], limit = 8, onSelect }) {
  if (!items.length) {
    return <p className="text-[13px] text-slate-500">Nothing yet. Run a scenario to see the agent work.</p>
  }
  return (
    <ul className="space-y-2.5">
      {items.slice(0, limit).map((item, i) => {
        const Icon = ICON[item.kind] ?? Activity
        const clickable = Boolean(item.incident_id && onSelect)
        const Tag = clickable ? 'button' : 'div'
        return (
          <li key={`${item.ts}-${i}`} className="animate-risein">
            <Tag
              {...(clickable ? { type: 'button', onClick: () => onSelect(item.incident_id) } : {})}
              className={`flex w-full items-start gap-2.5 rounded-lg px-1 py-0.5 text-left ${
                clickable ? 'hover:bg-white/[0.04]' : ''
              }`}
            >
              <Icon size={13} className="mt-0.5 shrink-0 text-agent/80" aria-hidden />
              <div className="min-w-0 flex-1">
                <p className="truncate text-[12.5px] text-slate-200">{item.title}</p>
                {item.detail && <p className="truncate text-[11px] text-slate-500">{item.detail}</p>}
              </div>
              <span className="num shrink-0 text-[10.5px] text-slate-600">{fmt.clock(item.ts)}</span>
            </Tag>
          </li>
        )
      })}
    </ul>
  )
}

import { Check, ShieldAlert, ShieldCheck, X } from 'lucide-react'
import { Field } from './Primitives'
import { fmt } from '../data/constants'

/**
 * Admissible(a, Σ) rendered clause by clause.
 *
 * The backend never collapses the four checks into one boolean, so neither does
 * this: a blocked action always names the specific guarantee that failed, which
 * is the property the audit trail depends on.
 */
export default function SafetyGate({ admissibility, command, compact = false }) {
  if (!admissibility) return null
  const ok = admissibility.admissible

  return (
    <div>
      <div
        className={[
          'flex items-center gap-3 rounded-xl border px-4 py-3',
          ok ? 'border-pass/35 bg-pass/[0.08]' : 'border-block/35 bg-block/[0.08]',
        ].join(' ')}
      >
        {ok ? (
          <ShieldCheck size={18} className="shrink-0 text-pass" aria-hidden />
        ) : (
          <ShieldAlert size={18} className="shrink-0 text-block" aria-hidden />
        )}
        <div className="min-w-0">
          <p className={`text-sm font-semibold ${ok ? 'text-pass' : 'text-block'}`}>
            {ok ? 'Action approved' : 'Action blocked'}
          </p>
          <p className="text-[12px] text-slate-400">
            {ok
              ? 'All four clauses of the safety envelope passed.'
              : `Failed: ${admissibility.failed_clauses.join(', ')}`}
          </p>
        </div>
      </div>

      <ul className="mt-3 space-y-1.5">
        {admissibility.checks.map((check) => (
          <li
            key={check.clause}
            className="flex items-start gap-3 rounded-lg border border-white/[0.06] bg-white/[0.02] px-3.5 py-2.5"
          >
            {check.passed ? (
              <span className="mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full bg-pass/15 text-pass">
                <Check size={10} aria-hidden />
              </span>
            ) : (
              <span className="mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full bg-block/15 text-block">
                <X size={10} aria-hidden />
              </span>
            )}
            <div className="min-w-0 flex-1">
              <p className="text-[12.5px] font-medium text-slate-200">{check.label}</p>
              <p className="num mt-0.5 break-words text-[11px] text-slate-500">{check.detail}</p>
            </div>
          </li>
        ))}
      </ul>

      {!compact && command && (
        <div className="mt-4 divide-y divide-white/[0.05] border-t border-white/[0.07] pt-2">
          <Field label="Endpoint" value={command.endpoint} />
          <Field label="Inverse a⁻¹" value={command.inverse_endpoint ?? 'none'} />
          <Field
            label="Rollback window"
            value={command.rollback_seconds !== null ? `~${fmt.num(command.rollback_seconds, 0)} s` : '—'}
          />
          <Field label="Signature" value={command.signature_preview} />
        </div>
      )}
    </div>
  )
}

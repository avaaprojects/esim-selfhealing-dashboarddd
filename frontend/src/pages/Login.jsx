import { useState } from 'react'
import { Eye, EyeOff, Gauge, Loader2, ShieldCheck, User } from 'lucide-react'
import { api, setToken } from '../services/api'

/**
 * The first screen anyone hitting the deployed link sees. Two demo roles;
 * neither credential pair is printed anywhere in the running app (see
 * README_DASHBOARD.md) — this is a DEMO ACCESS gate, not real identity
 * management, and is labelled as such.
 */
export default function Login({ onAuthenticated, notice = null }) {
  const [role, setRole] = useState(null) // 'client' | 'owner' | null
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const pickRole = (r) => {
    setRole(r)
    // The demo accounts happen to be called "owner" and "client"; this is only a
    // starting point, and any account of that role can be typed in instead.
    setUsername((current) => current || r)
    setPassword('')
    setError(null)
  }

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const result = await api.login(username, password)
      setToken(result.token)
      onAuthenticated(result)
    } catch (err) {
      // 429 is the lockout after repeated failures; its message already says
      // how long to wait, and is more useful than a generic failure line.
      setError(err.status === 429
        ? err.message
        : err.status === 401
          ? 'That username and password do not match an account.'
          : err.message || 'Sign-in failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid min-h-screen place-items-center px-4 py-10">
      <div className="w-full max-w-md">
        <div className="mb-8 flex flex-col items-center text-center">
          <span className="grid h-12 w-12 place-items-center rounded-2xl bg-agent/15 text-agent ring-1 ring-agent/30">
            <Gauge size={22} aria-hidden />
          </span>
          <h1 className="mt-4 text-lg font-semibold text-slate-100">
            Agentic Self-Healing eSIM / eUICC Dashboard
          </h1>
          <p className="mt-1.5 max-w-[38ch] text-[13px] leading-relaxed text-slate-500">
            How would you like to access the system?
          </p>
        </div>

        {notice && (
          <p className="mb-4 rounded-xl border border-hold/30 bg-hold/[0.08] px-4 py-2.5 text-[12.5px] text-hold">
            {notice}
          </p>
        )}

        {!role ? (
          <div className="grid gap-3 sm:grid-cols-2">
            <button
              type="button"
              onClick={() => pickRole('client')}
              className="glass group flex flex-col items-center gap-2.5 px-5 py-7 text-center transition-colors hover:border-agent/30"
            >
              <User size={20} className="text-agent" aria-hidden />
              <span className="text-[13.5px] font-semibold text-slate-100">Client / Viewer</span>
              <span className="text-[11.5px] leading-relaxed text-slate-500">
                Read-only monitoring: health, telemetry, incidents, safety decisions.
              </span>
            </button>
            <button
              type="button"
              onClick={() => pickRole('owner')}
              className="glass group flex flex-col items-center gap-2.5 px-5 py-7 text-center transition-colors hover:border-agent/30"
            >
              <ShieldCheck size={20} className="text-agent" aria-hidden />
              <span className="text-[13.5px] font-semibold text-slate-100">Owner / Administrator</span>
              <span className="text-[11.5px] leading-relaxed text-slate-500">
                Full control: run scenarios, real-time data, reset, memory, learning.
              </span>
            </button>
          </div>
        ) : (
          <form onSubmit={submit} className="glass space-y-4 p-6">
            <div className="flex items-center justify-between">
              <p className="text-[13px] font-semibold text-slate-100">
                {role === 'owner' ? 'Owner / Administrator sign-in' : 'Client / Viewer sign-in'}
              </p>
              <button
                type="button"
                onClick={() => setRole(null)}
                className="text-[11.5px] text-slate-500 hover:text-slate-300"
              >
                change
              </button>
            </div>

            <label className="block">
              <span className="label mb-1.5 block">Username</span>
              <input
                autoFocus
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2.5 text-[13px] text-slate-100 outline-none focus:border-agent/40"
              />
            </label>

            <label className="block">
              <span className="label mb-1.5 block">Password</span>
              <div className="relative">
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2.5 pr-10 text-[13px] text-slate-100 outline-none focus:border-agent/40"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  className="absolute inset-y-0 right-0 grid w-10 place-items-center text-slate-500 hover:text-slate-300"
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                >
                  {showPassword ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
            </label>

            {error && <p className="text-[12.5px] text-block">{error}</p>}

            <button
              type="submit"
              disabled={busy || !password}
              className="flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-agent to-cyan-300 px-4 py-2.5 text-[13px] font-semibold text-slate-950 transition-all hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy && <Loader2 size={14} className="animate-spin" aria-hidden />}
              Sign in
            </button>

            <p className="text-center text-[10.5px] text-slate-600">
              DEMO ACCESS · prototype credentials are documented in README_DASHBOARD.md,
              not shown here.
            </p>
          </form>
        )}
      </div>
    </div>
  )
}

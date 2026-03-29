import { useState } from 'react'
import { useAuth } from '../context/AuthContext'
import { BarChart3, LogIn, AlertCircle } from 'lucide-react'

export default function Login() {
  const { login } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await login(username, password)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-[var(--color-bg-primary)] flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="flex items-center justify-center gap-3 mb-8">
          <div className="w-10 h-10 rounded-xl bg-[var(--color-accent)]/20 flex items-center justify-center">
            <BarChart3 size={20} className="text-[var(--color-accent)]" />
          </div>
          <div>
            <div className="text-lg font-semibold text-[var(--color-text-primary)]">
              Trading Platform
            </div>
            <div className="text-xs text-[var(--color-text-secondary)] font-mono">
              Dashboard LIVE
            </div>
          </div>
        </div>

        {/* Form */}
        <form
          onSubmit={handleSubmit}
          className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-6 space-y-4"
        >
          <div>
            <label className="block text-xs text-[var(--color-text-secondary)] mb-1.5">
              Utilisateur
            </label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full px-3 py-2 rounded-lg bg-[var(--color-bg-primary)] border border-[var(--color-border)] text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-secondary)]/50 focus:outline-none focus:border-[var(--color-accent)]/50 transition-colors"
              placeholder="marc"
              autoComplete="username"
              required
            />
          </div>
          <div>
            <label className="block text-xs text-[var(--color-text-secondary)] mb-1.5">
              Mot de passe
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 rounded-lg bg-[var(--color-bg-primary)] border border-[var(--color-border)] text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-secondary)]/50 focus:outline-none focus:border-[var(--color-accent)]/50 transition-colors"
              placeholder="••••••••"
              autoComplete="current-password"
              required
            />
          </div>

          {error && (
            <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/20">
              <AlertCircle size={14} className="text-[var(--color-loss)] shrink-0" />
              <span className="text-xs text-[var(--color-loss)]">{error}</span>
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-[var(--color-accent)] text-white text-sm font-semibold hover:bg-[var(--color-accent)]/90 disabled:opacity-50 transition-all"
          >
            {loading ? (
              <span className="animate-pulse">Connexion...</span>
            ) : (
              <>
                <LogIn size={14} />
                Se connecter
              </>
            )}
          </button>
        </form>

        <div className="text-center mt-4 text-xs text-[var(--color-text-secondary)]">
          3 brokers &middot; 46 strategies &middot; V10 Risk Engine
        </div>
      </div>
    </div>
  )
}

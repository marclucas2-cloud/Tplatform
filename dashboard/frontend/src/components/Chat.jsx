import { useState, useRef, useEffect } from 'react'
import { MessageCircle, X, Send, Bot, User } from 'lucide-react'

const TOKEN_KEY = 'dashboard_token'

export default function Chat() {
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState([
    { role: 'assistant', content: 'Salut Marc ! Pose-moi une question sur le portfolio, les signaux, ou le risk.' },
  ])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const send = async () => {
    if (!input.trim() || loading) return
    const userMsg = { role: 'user', content: input.trim() }
    setMessages((prev) => [...prev, userMsg])
    setInput('')
    setLoading(true)

    try {
      const token = localStorage.getItem(TOKEN_KEY)
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          message: userMsg.content,
          history: messages.slice(-10),
        }),
      })
      if (res.status === 401) {
        window.location.href = '/login'
        return
      }
      const data = await res.json()
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: data.response || 'Pas de reponse.' },
      ])
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: `Erreur: ${e.message}` },
      ])
    } finally {
      setLoading(false)
    }
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="fixed bottom-5 right-5 z-50 w-12 h-12 rounded-full bg-[var(--color-accent)] text-white flex items-center justify-center shadow-lg hover:bg-[var(--color-accent)]/90 transition-all hover:scale-105"
      >
        <MessageCircle size={20} />
      </button>
    )
  }

  return (
    <div className="fixed bottom-5 right-5 z-50 w-[380px] h-[500px] bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl shadow-2xl flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)] bg-[var(--color-bg-primary)]">
        <div className="flex items-center gap-2">
          <Bot size={16} className="text-[var(--color-accent)]" />
          <span className="text-sm font-semibold text-[var(--color-text-primary)]">Assistant Trading</span>
          <span className="text-[10px] font-mono text-[var(--color-text-secondary)]">Haiku 4.5</span>
        </div>
        <button
          onClick={() => setOpen(false)}
          className="p-1 rounded hover:bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]"
        >
          <X size={16} />
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {messages.map((msg, i) => (
          <div key={i} className={`flex gap-2 ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            {msg.role === 'assistant' && (
              <div className="w-6 h-6 rounded-full bg-[var(--color-accent)]/20 flex items-center justify-center shrink-0 mt-0.5">
                <Bot size={12} className="text-[var(--color-accent)]" />
              </div>
            )}
            <div
              className={`max-w-[85%] px-3 py-2 rounded-xl text-sm leading-relaxed ${
                msg.role === 'user'
                  ? 'bg-[var(--color-accent)]/20 text-[var(--color-text-primary)]'
                  : 'bg-[var(--color-bg-hover)] text-[var(--color-text-primary)]'
              }`}
            >
              {msg.content.split('\n').map((line, j) => (
                <span key={j}>
                  {line}
                  {j < msg.content.split('\n').length - 1 && <br />}
                </span>
              ))}
            </div>
            {msg.role === 'user' && (
              <div className="w-6 h-6 rounded-full bg-[var(--color-bg-hover)] flex items-center justify-center shrink-0 mt-0.5">
                <User size={12} className="text-[var(--color-text-secondary)]" />
              </div>
            )}
          </div>
        ))}
        {loading && (
          <div className="flex gap-2">
            <div className="w-6 h-6 rounded-full bg-[var(--color-accent)]/20 flex items-center justify-center shrink-0">
              <Bot size={12} className="text-[var(--color-accent)]" />
            </div>
            <div className="px-3 py-2 rounded-xl bg-[var(--color-bg-hover)] text-sm text-[var(--color-text-secondary)] animate-pulse">
              Reflexion...
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="px-3 py-3 border-t border-[var(--color-border)]">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            send()
          }}
          className="flex gap-2"
        >
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Pose une question..."
            className="flex-1 px-3 py-2 rounded-lg bg-[var(--color-bg-primary)] border border-[var(--color-border)] text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-secondary)]/50 focus:outline-none focus:border-[var(--color-accent)]/50"
            disabled={loading}
          />
          <button
            type="submit"
            disabled={loading || !input.trim()}
            className="px-3 py-2 rounded-lg bg-[var(--color-accent)] text-white disabled:opacity-30 hover:bg-[var(--color-accent)]/90 transition-colors"
          >
            <Send size={14} />
          </button>
        </form>
      </div>
    </div>
  )
}

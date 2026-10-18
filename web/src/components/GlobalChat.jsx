import { useState, useRef, useEffect, useMemo, useCallback, Suspense, lazy } from 'react'
import { streamAsk, warmBackend } from '../utils/api'

// react-markdown only matters once an answer exists.
const ChatMarkdown = lazy(() => import('./ChatMarkdown'))

const STORAGE_KEY = 'edictai.conversations'

const PLACEHOLDERS = [
  'Which states have AI literacy laws?',
  'Compare California and Texas AI policies',
  'What bills were introduced in 2024?',
  'Which states have pending AI education bills?',
]

const SUGGESTIONS = [
  'Which states require AI literacy?',
  'Compare California and Texas policies',
  "What's the most common policy topic?",
  'Which states have pending legislation?',
]

function newConversation() {
  return { id: `c${Date.now()}${Math.random().toString(36).slice(2, 7)}`, name: 'New chat', messages: [] }
}

function loadConversations() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const parsed = raw ? JSON.parse(raw) : null
    if (Array.isArray(parsed) && parsed.length) return parsed
  } catch {
    // Corrupt or unavailable storage just means starting fresh.
  }
  return [newConversation()]
}

function GlobalChat({ onClose, style }) {
  const [convos, setConvos] = useState(loadConversations)
  const [activeId, setActiveId] = useState(() => convos[0].id)
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [waking, setWaking] = useState(false)
  const [placeholderIdx, setPlaceholderIdx] = useState(0)
  const [showList, setShowList] = useState(false)
  const messagesEndRef = useRef(null)
  const inputRef = useRef(null)

  const active = convos.find(c => c.id === activeId) || convos[0]
  // Memoised so the empty-array fallback isn't a new value on every render,
  // which would re-fire the scroll effect below continuously.
  const messages = useMemo(() => active?.messages || [], [active])

  // Conversations used to vanish on reload; now they survive it.
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(convos))
    } catch {
      // Over quota or blocked -- the in-memory conversation still works.
    }
  }, [convos])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  useEffect(() => {
    const timer = setInterval(() => {
      setPlaceholderIdx(i => (i + 1) % PLACEHOLDERS.length)
    }, 4000)
    return () => clearInterval(timer)
  }, [])

  const updateMessages = useCallback((id, updater) => {
    setConvos(prev => prev.map(c => (c.id === id ? { ...c, messages: updater(c.messages) } : c)))
  }, [])

  async function sendQuestion(q) {
    if (!q || loading) return
    const convoId = activeId

    if (messages.length === 0) {
      setConvos(prev => prev.map(c =>
        c.id === convoId ? { ...c, name: q.length > 34 ? `${q.slice(0, 34)}…` : q } : c,
      ))
    }

    updateMessages(convoId, prev => [...prev, { role: 'user', content: q }])
    setLoading(true)

    // If the dyno is still asleep, say so rather than showing a silent spinner
    // for 40 seconds.
    let settled = false
    warmBackend().then(() => { settled = true; setWaking(false) })
    const wakeTimer = setTimeout(() => { if (!settled) setWaking(true) }, 1500)

    // One placeholder message that fills in as deltas arrive.
    updateMessages(convoId, prev => [...prev, { role: 'assistant', content: '', streaming: true }])

    const writeAnswer = (text, done) => {
      updateMessages(convoId, prev => {
        const next = [...prev]
        const last = next.length - 1
        if (last >= 0) next[last] = { role: 'assistant', content: text, streaming: !done }
        return next
      })
    }

    try {
      const answer = await streamAsk(q, { onDelta: text => writeAnswer(text, false) })
      writeAnswer(answer, true)
    } catch {
      writeAnswer('Sorry, something went wrong. Please try again.', true)
    } finally {
      clearTimeout(wakeTimer)
      setWaking(false)
      setLoading(false)
    }
  }

  function handleSubmit(e) {
    e.preventDefault()
    const q = input.trim()
    if (!q) return
    setInput('')
    sendQuestion(q)
  }

  function handleNew() {
    const c = newConversation()
    setConvos(prev => [...prev, c])
    setActiveId(c.id)
    setShowList(false)
    inputRef.current?.focus()
  }

  function handleDelete(id) {
    const remaining = convos.filter(c => c.id !== id)
    if (remaining.length === 0) {
      const c = newConversation()
      setConvos([c])
      setActiveId(c.id)
    } else {
      setConvos(remaining)
      if (activeId === id) setActiveId(remaining[remaining.length - 1].id)
    }
  }

  const started = convos.filter(c => c.messages.length > 0)

  return (
    <aside className="drawer chat-drawer" style={style} aria-label="Ask about AI education policy">
      <div className="drawer-header chat-header">
        <h2 className="drawer-title chat-title">Ask Edictai</h2>
        <div className="chat-header-actions">
          {started.length > 0 && (
            <button
              className="text-btn"
              onClick={() => setShowList(s => !s)}
              aria-expanded={showList}
            >
              History
              <span className="chevron" aria-hidden="true">{showList ? '▴' : '▾'}</span>
            </button>
          )}
          <button className="text-btn" onClick={handleNew}>+ New</button>
          <button className="icon-btn" onClick={onClose} aria-label="Close chat">
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
              <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </button>
        </div>
      </div>

      {showList && started.length > 0 && (
        <ul className="chat-history">
          {started.map(c => (
            <li key={c.id} className={`chat-history-item${c.id === activeId ? ' is-active' : ''}`}>
              <button
                className="chat-history-name"
                onClick={() => { setActiveId(c.id); setShowList(false) }}
              >
                {c.name}
              </button>
              <button
                className="chat-history-delete"
                onClick={() => handleDelete(c.id)}
                aria-label={`Delete conversation: ${c.name}`}
              >
                &times;
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="chat-empty">
            <p className="chat-empty-text">
              Ask about AI education policy across all 50 states and DC.
            </p>
            <div className="chat-suggestions">
              {SUGGESTIONS.map(s => (
                <button
                  key={s}
                  className="chat-suggestion"
                  onClick={() => sendQuestion(s)}
                  disabled={loading}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`chat-msg chat-msg--${msg.role}`}>
            {msg.role === 'user' ? (
              msg.content
            ) : msg.content ? (
              <Suspense fallback={<span className="chat-plain">{msg.content}</span>}>
                <ChatMarkdown content={msg.content} />
              </Suspense>
            ) : (
              <span className="typing" aria-label="Thinking">
                <span className="typing-dot" />
                <span className="typing-dot" />
                <span className="typing-dot" />
              </span>
            )}
          </div>
        ))}

        {waking && (
          <p className="chat-notice">
            Waking the answer service — free hosting sleeps when idle, so this
            first question can take up to 30 seconds.
          </p>
        )}

        <div ref={messagesEndRef} />
      </div>

      <form className="chat-form" onSubmit={handleSubmit}>
        <div className="chat-input-wrap">
          <textarea
            ref={inputRef}
            className="chat-input"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                handleSubmit(e)
              }
            }}
            placeholder={PLACEHOLDERS[placeholderIdx]}
            disabled={loading}
            rows={1}
            aria-label="Ask a question"
          />
          <button
            type="submit"
            className="chat-send"
            disabled={loading || !input.trim()}
            aria-label="Send question"
          >
            <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
              <path d="M2 8h11M9 4l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        </div>
        <p className="chat-disclaimer">
          Answers are AI-generated and may be incomplete. Check linked sources.
        </p>
      </form>
    </aside>
  )
}

export default GlobalChat

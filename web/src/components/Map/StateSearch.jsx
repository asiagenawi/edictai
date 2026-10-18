import { useState, useRef, useEffect, useMemo } from 'react'
import { STATUS_COLORS, STATUS_LABELS } from '../../utils/colors'

/**
 * Type-ahead jump to a state.
 *
 * Small states are hard to hit on a choropleth and impossible to find without
 * knowing the map, so searching by name or postal code is the accessible path
 * to every state -- including from the keyboard.
 */
function StateSearch({ states, onSelectState }) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [highlight, setHighlight] = useState(0)
  const rootRef = useRef(null)
  const inputRef = useRef(null)

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return []
    return states
      .filter(s => s.name.toLowerCase().includes(q) || s.code.toLowerCase() === q)
      .slice(0, 6)
  }, [query, states])

  useEffect(() => {
    function onClickOutside(e) {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [])

  function choose(state) {
    onSelectState(state)
    setQuery('')
    setOpen(false)
    inputRef.current?.blur()
  }

  function onKeyDown(e) {
    if (!matches.length) {
      if (e.key === 'Escape') setOpen(false)
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlight(i => (i + 1) % matches.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlight(i => (i - 1 + matches.length) % matches.length)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      choose(matches[highlight])
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  const showList = open && matches.length > 0

  return (
    <div className="state-search" ref={rootRef}>
      <svg className="state-search-icon" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
        <circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
        <path d="M10.5 10.5 14 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
      <input
        ref={inputRef}
        type="text"
        className="state-search-input"
        placeholder="Find a state"
        value={query}
        onChange={e => { setQuery(e.target.value); setHighlight(0); setOpen(true) }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        role="combobox"
        aria-expanded={showList}
        aria-controls="state-search-list"
        aria-autocomplete="list"
        aria-label="Find a state"
      />
      {showList && (
        <ul className="state-search-list" id="state-search-list" role="listbox">
          {matches.map((s, i) => (
            <li key={s.code} role="option" aria-selected={i === highlight}>
              <button
                className={`state-search-option${i === highlight ? ' is-highlighted' : ''}`}
                onMouseEnter={() => setHighlight(i)}
                onClick={() => choose(s)}
              >
                <span
                  className="state-search-dot"
                  style={{ backgroundColor: STATUS_COLORS[s.policy_status || 'none'] }}
                />
                <span className="state-search-name">{s.name}</span>
                <span className="state-search-meta">{STATUS_LABELS[s.policy_status || 'none']}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default StateSearch

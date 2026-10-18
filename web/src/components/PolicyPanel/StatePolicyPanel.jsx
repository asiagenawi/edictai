import { useMemo } from 'react'
import { STATUS_COLORS, STATUS_DESCRIPTIONS } from '../../utils/colors'
import PolicyCard from './PolicyCard'

// Most consequential first, so the drawer opens on what matters.
const GROUP_ORDER = [
  { key: 'enacted', label: 'Enacted' },
  { key: 'active', label: 'In effect' },
  { key: 'introduced', label: 'Pending' },
  { key: 'failed', label: 'Failed' },
]

function StateDropdown({ states, selectedCode, onSelect }) {
  const sorted = useMemo(
    () => [...states].sort((a, b) => a.name.localeCompare(b.name)),
    [states],
  )
  return (
    <select
      className="state-select"
      value={selectedCode || ''}
      onChange={e => {
        const next = states.find(s => s.code === e.target.value)
        if (next) onSelect(next)
      }}
      aria-label="Jump to another state"
    >
      <option value="" disabled>Jump to state</option>
      {sorted.map(s => (
        <option key={s.code} value={s.code}>{s.name}</option>
      ))}
    </select>
  )
}

function StatePolicyPanel({ state, states = [], policies = [], onClose, onSelectState, style }) {
  const status = state?.policy_status || 'none'

  const groups = useMemo(() => {
    return GROUP_ORDER
      .map(g => ({ ...g, items: policies.filter(p => p.status === g.key) }))
      .filter(g => g.items.length > 0)
  }, [policies])

  // Anything with an unexpected status still has to appear somewhere.
  const ungrouped = useMemo(() => {
    const known = new Set(GROUP_ORDER.map(g => g.key))
    return policies.filter(p => !known.has(p.status))
  }, [policies])

  return (
    <aside className="drawer policy-drawer" style={style} aria-label={`${state.name} policies`}>
      <div className="drawer-header">
        <div className="drawer-title-row">
          <h2 className="drawer-title">{state.name}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close state panel">
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
              <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        <div className="drawer-meta">
          <span className="status-pill">
            <span className="status-pill-dot" style={{ backgroundColor: STATUS_COLORS[status] }} />
            {STATUS_DESCRIPTIONS[status]}
          </span>
          <span className="drawer-count">
            {policies.length} {policies.length === 1 ? 'policy' : 'policies'}
          </span>
        </div>

        {states.length > 0 && (
          <StateDropdown states={states} selectedCode={state.code} onSelect={onSelectState} />
        )}
      </div>

      <div className="drawer-body">
        {policies.length === 0 ? (
          <div className="empty-state">
            <h3 className="empty-title">No AI education policy on record</h3>
            <p className="empty-text">
              {state.name} has no tracked legislation, executive order, or department
              guidance on AI in education. That absence is itself a finding — ask the
              chat how neighbouring states are approaching it.
            </p>
          </div>
        ) : (
          <>
            {groups.map(group => (
              <section key={group.key} className="policy-group">
                <h3 className="policy-group-title">
                  {group.label}
                  <span className="policy-group-count">{group.items.length}</span>
                </h3>
                {group.items.map(p => <PolicyCard key={p.id} policy={p} />)}
              </section>
            ))}
            {ungrouped.length > 0 && (
              <section className="policy-group">
                <h3 className="policy-group-title">
                  Other
                  <span className="policy-group-count">{ungrouped.length}</span>
                </h3>
                {ungrouped.map(p => <PolicyCard key={p.id} policy={p} />)}
              </section>
            )}
          </>
        )}
      </div>
    </aside>
  )
}

export default StatePolicyPanel

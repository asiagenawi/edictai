const POLICY_TYPES = [
  { value: 'bill', label: 'Bills' },
  { value: 'guidance', label: 'Guidance' },
  { value: 'executive_order', label: 'Executive orders' },
]

/** Filters sit in one row above the charts, per the chart-composition rules. */
function FilterBar({ states, topics, filters, onChange, onReset, resultCount }) {
  const active = Boolean(filters.state || filters.topicId || filters.policyType)

  return (
    <div className="filter-bar">
      <select
        className="filter-select"
        value={filters.state || ''}
        onChange={e => onChange({ ...filters, state: e.target.value || null })}
        aria-label="Filter by state"
      >
        <option value="">All states</option>
        {[...states]
          .sort((a, b) => a.name.localeCompare(b.name))
          .map(s => <option key={s.code} value={s.code}>{s.name}</option>)}
      </select>

      <select
        className="filter-select"
        value={filters.topicId || ''}
        onChange={e => onChange({ ...filters, topicId: e.target.value || null })}
        aria-label="Filter by topic"
      >
        <option value="">All topics</option>
        {topics.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
      </select>

      <select
        className="filter-select"
        value={filters.policyType || ''}
        onChange={e => onChange({ ...filters, policyType: e.target.value || null })}
        aria-label="Filter by policy type"
      >
        <option value="">All types</option>
        {POLICY_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
      </select>

      <span className="filter-count">
        {resultCount} {resultCount === 1 ? 'policy' : 'policies'}
      </span>

      {active && (
        <button className="text-btn" onClick={onReset}>Clear filters</button>
      )}
    </div>
  )
}

export default FilterBar

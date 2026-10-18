import { useState, useMemo } from 'react'
import {
  COLUMNS, sortPolicies, filterPolicies, defaultDirection, jurisdictionOf,
} from '../../utils/table'
import { POLICY_STATUS_BADGES } from '../../utils/colors'
import DataActions from '../Layout/DataActions'

const TYPE_LABELS = {
  bill: 'Bill',
  guidance: 'Guidance',
  executive_order: 'Executive order',
}

const STATUS_LABELS = {
  enacted: 'Enacted',
  introduced: 'Pending',
  active: 'In effect',
  failed: 'Failed',
}

function formatDate(value) {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleDateString('en-US', { month: 'short', year: 'numeric', timeZone: 'UTC' })
}

function SortIcon({ active, direction }) {
  return (
    <svg
      className={`sort-icon${active ? ' sort-icon--active' : ''}`}
      viewBox="0 0 10 12"
      width="9"
      height="11"
      aria-hidden="true"
    >
      <path
        d="M5 1.5 7.6 5H2.4L5 1.5Z"
        fill="currentColor"
        opacity={!active || direction === 'asc' ? 1 : 0.28}
      />
      <path
        d="M5 10.5 2.4 7h5.2L5 10.5Z"
        fill="currentColor"
        opacity={!active || direction === 'desc' ? 1 : 0.28}
      />
    </svg>
  )
}

/**
 * Every tracked policy as one sortable, filterable table.
 *
 * The map answers "what is this state doing"; it cannot answer "show me
 * everything, ordered by when it happened". This is the view a researcher
 * actually works from, and the one the data can be taken away from.
 */
function PolicyTable({ snapshot, onSelectState }) {
  const [sort, setSort] = useState({ key: 'jurisdiction', direction: 'asc' })
  const [filters, setFilters] = useState({ query: '', type: '', status: '' })

  const rows = useMemo(() => {
    const filtered = filterPolicies(snapshot.policies, filters)
    return sortPolicies(filtered, sort.key, sort.direction)
  }, [snapshot.policies, filters, sort])

  function toggleSort(key) {
    setSort(prev => prev.key === key
      ? { key, direction: prev.direction === 'asc' ? 'desc' : 'asc' }
      : { key, direction: defaultDirection(key) })
  }

  function openState(policy) {
    if (!policy.state_code) return
    const state = snapshot.states.find(s => s.code === policy.state_code)
    if (state) onSelectState(state)
  }

  const filtering = Boolean(filters.query || filters.type || filters.status)

  if (snapshot.loading) return <div className="view-loading">Loading policies…</div>

  return (
    <div className="table-view">
      <div className="table-inner">
        <header className="table-header">
          <div>
            <h2 className="table-title">Every tracked policy</h2>
            <p className="table-subtitle">
              Sort any column. Select a row to open that jurisdiction on the map.
            </p>
          </div>
          <DataActions
            policies={rows}
            dataUpdated={snapshot.dataUpdated}
            filtered={filtering}
          />
        </header>

        <div className="table-controls">
          <input
            type="search"
            className="table-search"
            placeholder="Filter by title, bill number, summary, or state"
            value={filters.query}
            onChange={e => setFilters(f => ({ ...f, query: e.target.value }))}
            aria-label="Filter policies"
          />
          <select
            className="filter-select"
            value={filters.type}
            onChange={e => setFilters(f => ({ ...f, type: e.target.value }))}
            aria-label="Filter by type"
          >
            <option value="">All types</option>
            {Object.entries(TYPE_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <select
            className="filter-select"
            value={filters.status}
            onChange={e => setFilters(f => ({ ...f, status: e.target.value }))}
            aria-label="Filter by status"
          >
            <option value="">All statuses</option>
            {Object.entries(STATUS_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>

          <span className="table-count">
            {rows.length} of {snapshot.policies.length}
          </span>

          {filtering && (
            <button
              className="text-btn"
              onClick={() => setFilters({ query: '', type: '', status: '' })}
            >
              Clear
            </button>
          )}
        </div>

        <div className="table-scroll">
          <table className="policy-table">
            <thead>
              <tr>
                {COLUMNS.map(col => {
                  const active = sort.key === col.key
                  return (
                    <th
                      key={col.key}
                      style={{ width: col.width }}
                      aria-sort={active ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}
                    >
                      <button className="th-button" onClick={() => toggleSort(col.key)}>
                        {col.label}
                        <SortIcon active={active} direction={sort.direction} />
                      </button>
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {rows.map(p => {
                const badge = POLICY_STATUS_BADGES[p.status] || POLICY_STATUS_BADGES.active
                return (
                  <tr
                    key={p.id}
                    className={p.state_code ? 'is-clickable' : undefined}
                    onClick={() => openState(p)}
                    tabIndex={p.state_code ? 0 : -1}
                    onKeyDown={e => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        openState(p)
                      }
                    }}
                  >
                    <td className="cell-jurisdiction">{jurisdictionOf(p)}</td>
                    <td>
                      <span className="cell-title">{p.title}</span>
                      {p.bill_number && <span className="cell-bill">{p.bill_number}</span>}
                    </td>
                    <td className="cell-muted">{TYPE_LABELS[p.policy_type] || p.policy_type}</td>
                    <td>
                      <span
                        className="policy-badge"
                        style={{ backgroundColor: badge.bg, color: badge.text }}
                      >
                        {STATUS_LABELS[p.status] || p.status}
                      </span>
                    </td>
                    <td className="cell-date">{formatDate(p.date_introduced)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          {rows.length === 0 && (
            <p className="table-empty">
              No policies match those filters.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

export default PolicyTable

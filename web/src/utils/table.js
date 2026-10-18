/**
 * Sorting and filtering for the policy table.
 *
 * Kept as pure functions over the snapshot so the view stays presentational and
 * the ordering rules are testable on their own.
 */

export const COLUMNS = [
  { key: 'jurisdiction', label: 'Jurisdiction', width: '13%' },
  { key: 'title', label: 'Policy', width: '40%' },
  { key: 'policy_type', label: 'Type', width: '14%' },
  { key: 'status', label: 'Status', width: '13%' },
  { key: 'date_introduced', label: 'Introduced', width: '14%', numeric: true },
]

/** Federal rows have no state code, so they need a jurisdiction of their own. */
export function jurisdictionOf(policy) {
  return policy.state_name || 'Federal'
}

const TYPE_ORDER = { bill: 0, executive_order: 1, guidance: 2 }
// Most consequential first, so a status sort surfaces law rather than alphabet.
const STATUS_ORDER = { enacted: 0, active: 1, introduced: 2, failed: 3 }

function valueFor(policy, key) {
  switch (key) {
    case 'jurisdiction': return jurisdictionOf(policy)
    case 'policy_type': return TYPE_ORDER[policy.policy_type] ?? 99
    case 'status': return STATUS_ORDER[policy.status] ?? 99
    case 'date_introduced': return policy.date_introduced || ''
    default: return policy[key] ?? ''
  }
}

export function sortPolicies(policies, key, direction = 'asc') {
  const factor = direction === 'desc' ? -1 : 1
  return [...policies].sort((a, b) => {
    const av = valueFor(a, key)
    const bv = valueFor(b, key)

    let cmp
    if (typeof av === 'number' && typeof bv === 'number') cmp = av - bv
    else cmp = String(av).localeCompare(String(bv))

    // A stable secondary key, so equal rows don't reshuffle between sorts.
    if (cmp === 0) cmp = String(a.title).localeCompare(String(b.title))
    return cmp * factor
  })
}

export function filterPolicies(policies, { query = '', type = '', status = '' } = {}) {
  const q = query.trim().toLowerCase()
  return policies.filter(p => {
    if (type && p.policy_type !== type) return false
    if (status && p.status !== status) return false
    if (!q) return true
    return (
      String(p.title).toLowerCase().includes(q) ||
      String(p.bill_number || '').toLowerCase().includes(q) ||
      String(p.summary_text || '').toLowerCase().includes(q) ||
      jurisdictionOf(p).toLowerCase().includes(q)
    )
  })
}

/** Which way a column should sort the first time it's clicked. */
export function defaultDirection(key) {
  // Dates read newest-first by default; everything else reads A–Z.
  return key === 'date_introduced' ? 'desc' : 'asc'
}

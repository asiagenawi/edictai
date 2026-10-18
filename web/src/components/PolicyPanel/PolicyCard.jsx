import { POLICY_STATUS_BADGES } from '../../utils/colors'

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
  if (!value) return null
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleDateString('en-US', { month: 'short', year: 'numeric', timeZone: 'UTC' })
}

function PolicyCard({ policy }) {
  const badge = POLICY_STATUS_BADGES[policy.status] || POLICY_STATUS_BADGES.active
  const type = TYPE_LABELS[policy.policy_type] || policy.policy_type?.replace('_', ' ')
  const date = formatDate(policy.date_introduced)

  // Sans metadata under a serif title: the hierarchy does the work the old
  // all-serif card asked color and size to do alone.
  const meta = [type, policy.bill_number, date].filter(Boolean)

  return (
    <article className="policy-card" style={{ borderLeftColor: badge.accent }}>
      <div className="policy-card-head">
        <h4 className="policy-card-title">{policy.title}</h4>
        <span
          className="policy-badge"
          style={{ backgroundColor: badge.bg, color: badge.text }}
        >
          {STATUS_LABELS[policy.status] || policy.status}
        </span>
      </div>

      {meta.length > 0 && (
        <p className="policy-card-meta">
          {meta.map((part, i) => (
            <span key={part}>
              {i > 0 && <span className="meta-sep" aria-hidden="true">·</span>}
              {part}
            </span>
          ))}
        </p>
      )}

      {policy.summary_text && (
        <p className="policy-card-summary">{policy.summary_text}</p>
      )}

      {policy.source_url && (
        <a
          className="policy-card-link"
          href={policy.source_url}
          target="_blank"
          rel="noopener noreferrer"
        >
          View source
          <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true">
            <path
              d="M4.5 1.5h6v6M10.5 1.5 5 7M8 9.5v1h-6.5V4h1"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.3"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <span className="sr-only">(opens in a new tab)</span>
        </a>
      )}
    </article>
  )
}

export default PolicyCard

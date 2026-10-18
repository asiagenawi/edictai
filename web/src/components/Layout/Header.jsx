import StateSearch from '../Map/StateSearch'

const VIEWS = [
  { id: 'map', label: 'Map' },
  { id: 'table', label: 'Table' },
  { id: 'trends', label: 'Trends' },
]

function Header({ view, onViewChange, chatOpen, onToggleChat, snapshot, onSelectState, onOpenGuide }) {
  const updated = snapshot.dataUpdated

  return (
    <>
      <div className="utility-bar">
        <a className="skip-link" href="#main-content">Skip to content</a>
        <span>An independent tracker of AI in education policy</span>
        {updated && <span className="utility-sep">Data current to {formatUpdated(updated)}</span>}
      </div>

      <header className="app-header">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">
          <svg viewBox="0 0 26 26" width="24" height="24" fill="none">
            <rect x="1" y="14" width="6" height="11" fill="var(--status-guidance)" />
            <rect x="9.5" y="8" width="6" height="17" fill="var(--status-pending)" />
            <rect x="18" y="1" width="6" height="24" fill="var(--status-enacted)" />
          </svg>
        </span>
        <span className="brand-text">
          <span className="brand-name">Edictai</span>
          <span className="brand-tagline">AI in education policy</span>
        </span>
      </div>

      <nav className="view-nav" aria-label="Views">
        {VIEWS.map(v => (
          <button
            key={v.id}
            className={`view-tab${view === v.id ? ' view-tab--active' : ''}`}
            onClick={() => onViewChange(v.id)}
            aria-current={view === v.id ? 'page' : undefined}
          >
            {v.label}
          </button>
        ))}
      </nav>


      <div className="header-actions">
        <StateSearch states={snapshot.states} onSelectState={onSelectState} />
        <button
          className="guide-btn"
          onClick={onOpenGuide}
          aria-label="How to use this site"
          title="How to use this site"
        >
          <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
            <circle cx="8" cy="8" r="6.4" fill="none" stroke="currentColor" strokeWidth="1.4" />
            <path d="M6.2 6.1a1.85 1.85 0 1 1 2.1 1.85v1.2" fill="none" stroke="currentColor"
              strokeWidth="1.4" strokeLinecap="round" />
            <circle cx="8.3" cy="11.4" r="0.8" fill="currentColor" />
          </svg>
        </button>
        <button
          className={`ask-btn${chatOpen ? ' ask-btn--active' : ''}`}
          onClick={onToggleChat}
          aria-expanded={chatOpen}
        >
          <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
            <path
              d="M8 1.5c3.6 0 6.5 2.4 6.5 5.4S11.6 12.3 8 12.3c-.5 0-1-.05-1.5-.14L3 14l.7-2.6C2.35 10.4 1.5 9 1.5 6.9 1.5 3.9 4.4 1.5 8 1.5Z"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinejoin="round"
            />
          </svg>
          {chatOpen ? 'Close' : 'Ask'}
        </button>
      </div>
      </header>
    </>
  )
}

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December']

function formatUpdated(value) {
  const [year, month] = String(value).split('-')
  const name = MONTHS[Number(month) - 1]
  return name ? `${name} ${year}` : year
}

export default Header

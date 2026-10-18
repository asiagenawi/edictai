import { useState, useMemo } from 'react'
import { useTrends } from '../../hooks/useTrends'
import { STATUS_COLORS } from '../../utils/colors'
import PolicyTimeline from './PolicyTimeline'
import CategoryBreakdown from './CategoryBreakdown'
import FilterBar from './FilterBar'

// Maps a raw policy status onto the map's own encoding, so a bar and a state
// that mean the same thing are the same colour.
const STATUS_TONE = {
  enacted: 'enacted',
  active: 'guidance',
  introduced: 'pending',
  failed: 'failed',
}

const STATUS_LABELS = {
  enacted: 'Enacted',
  active: 'In effect',
  introduced: 'Pending',
  failed: 'Failed',
}

const EMPTY_FILTERS = { state: null, topicId: null, policyType: null }

function TrendsView({ snapshot }) {
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const trends = useTrends(snapshot, filters)

  const peakYear = useMemo(() => {
    if (!trends.timeline.length) return null
    return trends.timeline.reduce((a, b) => (b.count > a.count ? b : a))
  }, [trends.timeline])

  if (snapshot.loading) {
    return <div className="view-loading">Loading trends…</div>
  }

  return (
    <div className="trends-view">
      <div className="trends-inner">
        <header className="trends-header">
          <h2 className="trends-title">How AI education policy is moving</h2>
          <p className="trends-subtitle">
            Every tracked bill, executive order, and department guidance document,
            across all 50 states and DC.
          </p>
        </header>

        <FilterBar
          states={snapshot.states}
          topics={snapshot.topics}
          filters={filters}
          onChange={setFilters}
          onReset={() => setFilters(EMPTY_FILTERS)}
          resultCount={trends.total}
        />

        <div className="tile-row">
          <div className="tile">
            <span className="tile-value">{trends.total}</span>
            <span className="tile-label">policies tracked</span>
          </div>
          <div className="tile">
            <span className="tile-value">{trends.statesActing}</span>
            <span className="tile-label">states represented</span>
          </div>
          <div className="tile">
            <span className="tile-value">{peakYear ? peakYear.count : 0}</span>
            <span className="tile-label">
              peak year{peakYear ? ` (${peakYear.year})` : ''}
            </span>
          </div>
          <div className="tile">
            <span className="tile-value">{trends.topicCounts.length}</span>
            <span className="tile-label">topics covered</span>
          </div>
        </div>

        <div className="panel-grid">
          <section className="chart-panel">
            <h3 className="chart-title">Policies introduced per year</h3>
            <PolicyTimeline data={trends.timeline} />
            {trends.undated > 0 && (
              <p className="chart-note">
                {trends.undated} further {trends.undated === 1 ? 'policy is' : 'policies are'} tracked
                without a confirmed introduction date and {trends.undated === 1 ? 'is' : 'are'} not
                plotted here. They still appear in the table and in every count.
              </p>
            )}
          </section>

          <section className="chart-panel">
            <h3 className="chart-title">Status of tracked policies</h3>
            <ul className="status-list">
              {trends.statusBreakdown.map(s => {
                const tone = STATUS_TONE[s.name] || 'guidance'
                const pct = trends.total ? (s.count / trends.total) * 100 : 0
                return (
                  <li key={s.name} className="status-row">
                    <span className="status-row-label">
                      {STATUS_LABELS[s.name] || s.name}
                    </span>
                    <span className="status-row-track">
                      <span
                        className="status-row-fill"
                        style={{ width: `${pct}%`, backgroundColor: STATUS_COLORS[tone] }}
                      />
                    </span>
                    <span className="status-row-value">{s.count}</span>
                  </li>
                )
              })}
            </ul>
          </section>

          <section className="chart-panel chart-panel--wide">
            <h3 className="chart-title">Policies by topic</h3>
            <CategoryBreakdown data={trends.topicCounts} />
          </section>
        </div>
      </div>
    </div>
  )
}

export default TrendsView

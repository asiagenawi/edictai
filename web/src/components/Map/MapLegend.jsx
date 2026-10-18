import { STATUS_COLORS, STATUS_ORDER, STATUS_LABELS } from '../../utils/colors'

/**
 * The legend carries the relief for the two lightest fills, which sit below 3:1
 * against the surface -- color never has to work alone here.
 */
function MapLegend({ loading }) {
  return (
    <div className={`map-legend${loading ? ' map-legend--loading' : ''}`}>
      <span className="legend-title">Most significant action</span>
      <div className="legend-scale">
        {STATUS_ORDER.map(status => (
          <div key={status} className="legend-item">
            <span
              className="legend-swatch"
              style={{ backgroundColor: STATUS_COLORS[status] }}
            />
            <span className="legend-label">{STATUS_LABELS[status]}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export default MapLegend

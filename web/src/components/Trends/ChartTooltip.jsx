/** Shared tooltip body so every chart in the view speaks the same way. */
function ChartTooltip({ active, payload, label, unit = 'policies' }) {
  if (!active || !payload?.length) return null
  const value = payload[0].value
  return (
    <div className="chart-tooltip">
      <span className="chart-tooltip-label">{label}</span>
      <span className="chart-tooltip-value">
        {value} {value === 1 ? unit.replace(/ies$/, 'y').replace(/s$/, '') : unit}
      </span>
    </div>
  )
}

export default ChartTooltip

import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, LabelList,
} from 'recharts'
import ChartTooltip from './ChartTooltip'
import { CHART, TICK, VALUE_LABEL } from './chartTheme'

/**
 * Topics ranked by how many policies touch them. Horizontal because the topic
 * names are long -- rotated axis labels are an anti-pattern.
 */
function CategoryBreakdown({ data }) {
  if (!data.length) return <p className="chart-empty">No topics match these filters.</p>

  return (
    <ResponsiveContainer width="100%" height={Math.max(220, data.length * 30)}>
      <BarChart
        data={data}
        layout="vertical"
        margin={{ top: 4, right: 40, bottom: 4, left: 16 }}
        barCategoryGap="26%"
      >
        <XAxis type="number" hide allowDecimals={false} />
        <YAxis
          type="category"
          dataKey="name"
          tickLine={false}
          axisLine={false}
          tick={TICK}
          width={148}
        />
        <Tooltip content={<ChartTooltip />} cursor={{ fill: CHART.cursor }} />
        <Bar dataKey="count" fill={CHART.seriesStrong} maxBarSize={15} isAnimationActive={false}>
          <LabelList dataKey="count" position="right" offset={7} {...VALUE_LABEL} />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

export default CategoryBreakdown

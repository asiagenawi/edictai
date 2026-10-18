import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, LabelList,
} from 'recharts'
import ChartTooltip from './ChartTooltip'
import { CHART, TICK, VALUE_LABEL } from './chartTheme'

/**
 * Policies introduced per year. One series, so the panel title names it and no
 * legend is needed; values are direct-labelled rather than read off the axis.
 */
function PolicyTimeline({ data }) {
  if (!data.length) return <p className="chart-empty">No dated policies match these filters.</p>

  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={data} margin={{ top: 18, right: 20, bottom: 4, left: -4 }} barCategoryGap="28%">
        <CartesianGrid vertical={false} stroke={CHART.grid} strokeDasharray="0" />
        <XAxis
          dataKey="year"
          tickLine={false}
          axisLine={{ stroke: CHART.axis }}
          tick={TICK}
          dy={4}
        />
        <YAxis
          tickLine={false}
          axisLine={false}
          tick={TICK}
          allowDecimals={false}
          width={44}
        />
        <Tooltip content={<ChartTooltip />} cursor={{ fill: CHART.cursor }} />
        <Bar dataKey="count" fill={CHART.series} maxBarSize={56} isAnimationActive={false}>
          <LabelList dataKey="count" position="top" offset={7} {...VALUE_LABEL} />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

export default PolicyTimeline

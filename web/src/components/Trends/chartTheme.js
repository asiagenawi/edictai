/**
 * One chart vocabulary, matching the interface around it.
 *
 * Square marks and hairline rules, because the rest of the design has no radius
 * and separates with rules. The series colours are the choropleth's own steps,
 * so a bar and a state that mean the same thing look the same.
 */
export const CHART = {
  series: '#357d59',
  seriesStrong: '#063d29',
  grid: '#d5dbd6',
  axis: '#b4bfb7',
  axisText: '#76837a',
  labelText: '#47544b',
  cursor: 'rgba(6, 61, 41, 0.06)',
}

/** Serif figures, tabular, so axis and value labels line up. */
export const TICK = {
  fill: CHART.axisText,
  fontSize: 11,
  fontFamily: "'IBM Plex Serif', Georgia, serif",
}

export const VALUE_LABEL = {
  fill: CHART.labelText,
  fontSize: 11,
  fontFamily: "'Bitter', Georgia, serif",
  fontWeight: 600,
}

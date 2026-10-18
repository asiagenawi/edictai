/**
 * Choropleth encoding for state policy status.
 *
 * The five statuses are ordinal, not arbitrary categories -- they mirror the
 * precedence in backend/models/queries.py:get_all_states(), where a state is
 * labelled by the most significant action it has taken:
 *
 *   enacted > pending > guidance > failed > none
 *
 * So three of them ride a single-hue sequential ramp (more ink = more binding
 * policy in force), "failed" sits off-ramp in orange because it is an outcome
 * rather than a level, and "none" is a near-surface neutral meaning "nothing to
 * report". Deliberately no red/blue opposition: on a US state map that reads as
 * partisan, which this data is not.
 *
 * Validated as an ordinal ramp (monotone lightness, all step gaps >= 0.06,
 * light end 2.06:1 vs surface) and for all-pairs separation: worst CVD dE 14.0
 * and worst normal-vision dE 15.6, both clear of the 8 / 15 floors.
 *
 * The lightest two fills sit below 3:1 against the surface, so the relief rule
 * applies -- they never carry meaning alone. Every state also has a text label
 * in the legend, an accessible name on the shape, a tooltip, and the policy
 * panel as the table view.
 */
export const STATUS_COLORS = {
  none: '#e4e6e4',
  failed: '#c2410c',
  guidance: '#86b39d',
  pending: '#357d59',
  enacted: '#063d29',
}

/** Legend order: escalating, least action -> most action. */
export const STATUS_ORDER = ['none', 'failed', 'guidance', 'pending', 'enacted']

export const STATUS_LABELS = {
  none: 'No policy',
  failed: 'Failed',
  guidance: 'Guidance only',
  pending: 'Pending',
  enacted: 'Enacted',
}

/** Longer phrasing for tooltips and accessible names. */
export const STATUS_DESCRIPTIONS = {
  none: 'No policy yet',
  failed: 'Failed legislation',
  guidance: 'Guidance only',
  pending: 'Pending bills',
  enacted: 'Enacted legislation',
}

/**
 * Ink color for a label drawn on top of a status fill. The ramp spans a wide
 * lightness range, so a single label color cannot stay legible across it.
 */
export function labelInkOn(status) {
  // Measured per fill: white clears 4.5:1 on failed (5.18), pending (4.97) and
  // enacted (12.30); the two pale fills take dark ink.
  return ['failed', 'pending', 'enacted'].includes(status) ? '#ffffff' : '#0d1b12'
}

/** Per-status badge colors for policy cards, keyed by the raw policy.status. */
export const POLICY_STATUS_BADGES = {
  enacted: { bg: 'transparent', text: '#063d29', accent: '#063d29' },
  introduced: { bg: 'transparent', text: '#357d59', accent: '#357d59' },
  active: { bg: 'transparent', text: '#2c6b4c', accent: '#86b39d' },
  failed: { bg: 'transparent', text: '#9a340a', accent: '#c2410c' },
}

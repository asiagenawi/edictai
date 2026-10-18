const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December']

const SITE_URL = 'asiagenawi.github.io/statescope'

function formatMonth(value) {
  if (!value) return null
  const [year, month] = value.split('-')
  const name = MONTHS[Number(month) - 1]
  return name ? `${name} ${year}` : year
}

function today(now = new Date()) {
  return `${now.getDate()} ${MONTHS[now.getMonth()].slice(0, 3)} ${now.getFullYear()}`
}

/**
 * A citation carrying both dates that matter: when the data was curated, and
 * when the reader retrieved it. A tracker that only gives one of those cannot
 * be cited honestly, because the page changes and the data does not.
 */
export function buildCitation(dataUpdated, now = new Date()) {
  const curated = formatMonth(dataUpdated)
  return [
    'Edictai: AI in Education Policy Tracker.',
    curated ? `Data current to ${curated}.` : null,
    `Retrieved ${today(now)} from ${SITE_URL}`,
  ].filter(Boolean).join(' ')
}

import { jurisdictionOf } from './table.js'

const COLUMNS = [
  ['jurisdiction', 'Jurisdiction'],
  ['state_code', 'State code'],
  ['title', 'Title'],
  ['bill_number', 'Bill number'],
  ['policy_type', 'Type'],
  ['status', 'Status'],
  ['level', 'Level'],
  ['date_introduced', 'Introduced'],
  ['date_enacted', 'Enacted'],
  ['sponsor', 'Sponsor'],
  ['summary_text', 'Summary'],
  ['source_url', 'Source URL'],
]

/** RFC 4180: quote every field, double any embedded quotes. */
function cell(value) {
  if (value == null) return '""'
  return `"${String(value).replace(/"/g, '""')}"`
}

export function policiesToCSV(policies) {
  const header = COLUMNS.map(([, label]) => cell(label)).join(',')
  const rows = policies.map(p =>
    COLUMNS.map(([key]) => cell(key === 'jurisdiction' ? jurisdictionOf(p) : p[key])).join(','),
  )
  return [header, ...rows].join('\r\n')
}

export function downloadCSV(policies, filename = 'edictai-policies.csv') {
  // The BOM stops Excel mangling non-ASCII in policy titles.
  const blob = new Blob(['﻿', policiesToCSV(policies)], {
    type: 'text/csv;charset=utf-8;',
  })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

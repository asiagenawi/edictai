/**
 * Whether the welcome guide has already been dismissed on this browser.
 *
 * Lives outside the component so the component file only exports a component,
 * which is what keeps fast refresh working.
 */
export const GUIDE_STORAGE_KEY = 'edictai.guide.v1'

export function hasSeenGuide() {
  try {
    return localStorage.getItem(GUIDE_STORAGE_KEY) === '1'
  } catch {
    // Blocked storage (private mode, blocked site data): showing the guide is
    // the safe default.
    return false
  }
}

export function markGuideSeen() {
  try {
    localStorage.setItem(GUIDE_STORAGE_KEY, '1')
  } catch {
    // Failing to remember the dismissal shouldn't prevent dismissing it.
  }
}

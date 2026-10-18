import { useMemo } from 'react'

/**
 * Trend aggregates, computed from the snapshot in the browser.
 *
 * These used to be four /api/trends/* round trips. The dataset is 75 rows, so
 * grouping it locally is faster than asking the server to do it and removes the
 * last reason for the Trends view to touch the network.
 */
export function useTrends({ policies, policyTopics, topics }, filters = {}) {
  const { state, topicId, policyType } = filters

  const filtered = useMemo(() => {
    return policies.filter(p => {
      if (state && p.state_code !== state) return false
      if (policyType && p.policy_type !== policyType) return false
      if (topicId) {
        const ids = policyTopics[String(p.id)] || []
        if (!ids.includes(Number(topicId))) return false
      }
      return true
    })
  }, [policies, policyTopics, state, topicId, policyType])

  const timeline = useMemo(() => {
    const byYear = new Map()
    for (const p of filtered) {
      if (!p.date_introduced) continue
      const year = String(p.date_introduced).slice(0, 4)
      byYear.set(year, (byYear.get(year) || 0) + 1)
    }
    return [...byYear.entries()]
      .map(([year, count]) => ({ year, count }))
      .sort((a, b) => a.year.localeCompare(b.year))
  }, [filtered])

  const topicCounts = useMemo(() => {
    const nameById = new Map(topics.map(t => [t.id, t.name]))
    const counts = new Map()
    for (const p of filtered) {
      for (const id of policyTopics[String(p.id)] || []) {
        counts.set(id, (counts.get(id) || 0) + 1)
      }
    }
    return [...counts.entries()]
      .map(([id, count]) => ({ id, name: nameById.get(id) || 'Unknown', count }))
      .sort((a, b) => b.count - a.count)
  }, [filtered, topics, policyTopics])

  const undated = useMemo(
    () => filtered.filter(p => !p.date_introduced).length,
    [filtered],
  )

  const statusBreakdown = useMemo(() => countBy(filtered, p => p.status), [filtered])
  const typeBreakdown = useMemo(() => countBy(filtered, p => p.policy_type), [filtered])
  const levelBreakdown = useMemo(() => countBy(filtered, p => p.level), [filtered])

  const statesActing = useMemo(
    () => new Set(filtered.filter(p => p.state_code).map(p => p.state_code)).size,
    [filtered],
  )

  return {
    filtered,
    timeline,
    topicCounts,
    statusBreakdown,
    typeBreakdown,
    levelBreakdown,
    statesActing,
    undated,
    total: filtered.length,
  }
}

function countBy(rows, keyOf) {
  const counts = new Map()
  for (const row of rows) {
    const key = keyOf(row)
    if (!key) continue
    counts.set(key, (counts.get(key) || 0) + 1)
  }
  return [...counts.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count)
}

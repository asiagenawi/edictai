import { useState, useEffect, useMemo } from 'react'

/**
 * The policy dataset, shipped as a static build artifact rather than fetched
 * from the API.
 *
 * `statescope build` (pipeline/) generates public/data/snapshot.json at
 * build time from the same queries the API serves. The data is derived from
 * checked-in YAML records and never mutates at runtime, so reading it directly is
 * equivalent to calling /api/states -- minus a network round trip, and minus
 * waiting 30-60s for the free-tier backend to cold start.
 */
const SNAPSHOT_URL = `${import.meta.env.BASE_URL}data/snapshot.json`

// Module-level so the request is shared by every consumer and fires once, even
// if several components mount before it resolves.
let snapshotPromise = null

function loadSnapshot() {
  if (!snapshotPromise) {
    snapshotPromise = fetch(SNAPSHOT_URL).then(res => {
      if (!res.ok) throw new Error(`Failed to load policy data: ${res.status}`)
      return res.json()
    })
  }
  return snapshotPromise
}

export function useSnapshot() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let active = true
    loadSnapshot().then(
      d => { if (active) setData(d) },
      e => { if (active) setError(e) },
    )
    return () => { active = false }
  }, [])

  const states = data?.states
  const policies = data?.policies

  const stateByFips = useMemo(() => {
    const map = {}
    for (const s of states || []) map[s.fips] = s
    return map
  }, [states])

  // Grouped once here so selecting a state is a lookup rather than a request.
  const policiesByState = useMemo(() => {
    const map = {}
    for (const p of policies || []) {
      if (!p.state_code) continue
      ;(map[p.state_code] ||= []).push(p)
    }
    return map
  }, [policies])

  return {
    states: states || [],
    policies: policies || [],
    topics: data?.topics || [],
    policyTopics: data?.policy_topics || {},
    dataUpdated: data?.data_updated,
    stateByFips,
    policiesByState,
    loading: !data && !error,
    error,
  }
}

import { useState, useEffect } from 'react'

/**
 * The US topojson, fetched and parsed exactly once.
 *
 * react-simple-maps' useGeographies has no cache -- it fetches per <Geographies>
 * instance -- so the main map and the Northeast inset each pulled and parsed the
 * same 114KB file. Passing the parsed object instead of a URL takes the
 * isString(geography) === false branch, which skips the fetch entirely.
 */
const GEO_URL = `${import.meta.env.BASE_URL}us-states-10m.json`

let geoPromise = null

function loadGeo() {
  if (!geoPromise) {
    geoPromise = fetch(GEO_URL).then(res => {
      if (!res.ok) throw new Error(`Failed to load map geometry: ${res.status}`)
      return res.json()
    })
  }
  return geoPromise
}

export function useGeoData() {
  const [geo, setGeo] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let active = true
    loadGeo().then(
      g => { if (active) setGeo(g) },
      e => { if (active) setError(e) },
    )
    return () => { active = false }
  }, [])

  return { geo, error, loading: !geo && !error }
}

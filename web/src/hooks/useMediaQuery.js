import { useSyncExternalStore } from 'react'

/**
 * useSyncExternalStore is the right tool here: matchMedia is an external store,
 * and subscribing to it directly avoids the render-then-setState-in-effect
 * cascade the previous implementation caused on every mount.
 */
export function useMediaQuery(query) {
  const mql = window.matchMedia(query)

  return useSyncExternalStore(
    callback => {
      mql.addEventListener('change', callback)
      return () => mql.removeEventListener('change', callback)
    },
    () => mql.matches,
    () => false, // server render: assume desktop
  )
}

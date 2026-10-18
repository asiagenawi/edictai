import { ComposableMap, Geographies, Geography } from 'react-simple-maps'
import { STATUS_COLORS, STATUS_DESCRIPTIONS } from '../../utils/colors'

const NORTHEAST_FIPS = new Set([
  '09', // CT
  '10', // DE
  '11', // DC
  '24', // MD
  '25', // MA
  '23', // ME
  '33', // NH
  '34', // NJ
  '36', // NY
  '42', // PA
  '44', // RI
  '50', // VT
])

/**
 * Magnified Northeast. Receives the already-parsed topojson from USMap -- when
 * this passed a URL instead, react-simple-maps fetched and parsed the same
 * 114KB file a second time.
 */
function NortheastInset({
  geo,
  stateByFips,
  selectedState,
  onMouseEnter,
  onMouseLeave,
  onMouseMove,
  onKeyDown,
  onClick,
}) {
  return (
    <div className="northeast-inset">
      <div className="northeast-inset-label">Northeast, enlarged</div>
      <ComposableMap
        projection="geoMercator"
        projectionConfig={{ center: [-73.7, 42.6], scale: 780 }}
        width={200}
        height={170}
        viewBox="0 0 200 170"
        className="northeast-inset-map"
        role="group"
        aria-label="Northeast states, enlarged"
      >
        <Geographies geography={geo}>
          {({ geographies }) =>
            geographies
              .filter(geoItem => NORTHEAST_FIPS.has(geoItem.id))
              .map(geoItem => {
                const state = stateByFips[geoItem.id]
                const status = state?.policy_status || 'none'
                const isSelected = selectedState?.code === state?.code
                const label = state
                  ? `${state.name}. ${STATUS_DESCRIPTIONS[status]}.`
                  : undefined
                return (
                  <Geography
                    key={geoItem.rsmKey}
                    geography={geoItem}
                    className={`state-shape${isSelected ? ' state-shape--selected' : ''}`}
                    fill={STATUS_COLORS[status]}
                    tabIndex={state ? 0 : -1}
                    role={state ? 'button' : undefined}
                    aria-label={label}
                    aria-pressed={state ? isSelected : undefined}
                    onMouseEnter={evt => onMouseEnter(geoItem, evt)}
                    onMouseMove={onMouseMove}
                    onMouseLeave={onMouseLeave}
                    onFocus={evt => onMouseEnter(geoItem, evt)}
                    onBlur={onMouseLeave}
                    onKeyDown={evt => onKeyDown(evt, geoItem)}
                    onClick={() => onClick(geoItem)}
                  />
                )
              })
          }
        </Geographies>
      </ComposableMap>
    </div>
  )
}

export default NortheastInset

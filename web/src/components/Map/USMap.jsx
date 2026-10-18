import { useState, useCallback } from 'react'
import { ComposableMap, Geographies, Geography, Marker } from 'react-simple-maps'
import { geoCentroid } from 'd3-geo'
import { useGeoData } from '../../hooks/useGeoData'
import { STATUS_COLORS, STATUS_DESCRIPTIONS, labelInkOn } from '../../utils/colors'
import MapLegend from './MapLegend'
import NortheastInset from './NortheastInset'
import StateTooltip from './StateTooltip'

/**
 * States too small to hold a label at this projection scale. They are covered
 * by the Northeast inset instead, which has the room.
 */
const NO_LABEL_FIPS = new Set([
  '09', '10', '11', '24', '25', '33', '34', '44', '50',
])

function accessibleName(state) {
  if (!state) return 'Unknown area'
  const status = STATUS_DESCRIPTIONS[state.policy_status || 'none']
  const count = state.policy_count || 0
  const policies = count === 1 ? '1 policy' : `${count} policies`
  return `${state.name}. ${status}. ${policies}.`
}

function USMap({ snapshot, selectedState, onSelectState }) {
  const { geo, error: geoError } = useGeoData()
  const { stateByFips, error: dataError, loading: dataLoading } = snapshot

  const [hoveredState, setHoveredState] = useState(null)
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 })

  const handleMouseEnter = useCallback((geoItem, evt) => {
    const state = stateByFips[geoItem.id]
    if (state) {
      setHoveredState(state)
      setTooltipPos({ x: evt.clientX, y: evt.clientY })
    }
  }, [stateByFips])

  const handleMouseMove = useCallback(evt => {
    setTooltipPos({ x: evt.clientX, y: evt.clientY })
  }, [])

  const handleMouseLeave = useCallback(() => setHoveredState(null), [])

  const handleClick = useCallback(geoItem => {
    const state = stateByFips[geoItem.id]
    if (state) onSelectState(state)
  }, [stateByFips, onSelectState])

  const handleKeyDown = useCallback((evt, geoItem) => {
    if (evt.key !== 'Enter' && evt.key !== ' ') return
    evt.preventDefault()
    handleClick(geoItem)
  }, [handleClick])

  const error = geoError || dataError
  if (error) {
    return (
      <div className="stage-message">
        <h2>Couldn’t load the map</h2>
        <p>{error.message}</p>
      </div>
    )
  }

  return (
    <div className="map-stage">
      <div className="map-wrapper">
        {!geo && <div className="map-skeleton" aria-hidden="true" />}

        {geo && (
          <ComposableMap
            projection="geoAlbersUsa"
            projectionConfig={{ scale: 980 }}
            width={960}
            height={640}
            viewBox="0 0 960 640"
            className="composable-map"
            role="group"
            aria-label="US map of state AI education policy status"
          >
            <Geographies geography={geo}>
              {({ geographies, projection }) => (
                <>
                  {geographies.map(geoItem => {
                    const state = stateByFips[geoItem.id]
                    const status = state?.policy_status || 'none'
                    const isSelected = selectedState?.code === state?.code
                    return (
                      <Geography
                        key={geoItem.rsmKey}
                        geography={geoItem}
                        className={`state-shape${isSelected ? ' state-shape--selected' : ''}`}
                        fill={STATUS_COLORS[status]}
                        tabIndex={state ? 0 : -1}
                        role={state ? 'button' : undefined}
                        aria-label={state ? accessibleName(state) : undefined}
                        aria-pressed={state ? isSelected : undefined}
                        onMouseEnter={evt => handleMouseEnter(geoItem, evt)}
                        onMouseMove={handleMouseMove}
                        onMouseLeave={handleMouseLeave}
                        onFocus={evt => handleMouseEnter(geoItem, evt)}
                        onBlur={handleMouseLeave}
                        onKeyDown={evt => handleKeyDown(evt, geoItem)}
                        onClick={() => handleClick(geoItem)}
                      />
                    )
                  })}

                  {/* Postal codes so the map is readable without hovering. */}
                  {geographies.map(geoItem => {
                    const state = stateByFips[geoItem.id]
                    if (!state || NO_LABEL_FIPS.has(geoItem.id)) return null
                    const centroid = geoCentroid(geoItem)
                    if (!centroid || !projection(centroid)) return null
                    return (
                      <Marker key={`label-${geoItem.rsmKey}`} coordinates={centroid}>
                        <text
                          className="state-label"
                          textAnchor="middle"
                          dy="0.33em"
                          fill={labelInkOn(state.policy_status || 'none')}
                        >
                          {state.code}
                        </text>
                      </Marker>
                    )
                  })}
                </>
              )}
            </Geographies>
          </ComposableMap>
        )}

        {geo && (
          <NortheastInset
            geo={geo}
            stateByFips={stateByFips}
            selectedState={selectedState}
            onMouseEnter={handleMouseEnter}
            onMouseMove={handleMouseMove}
            onMouseLeave={handleMouseLeave}
            onKeyDown={handleKeyDown}
            onClick={handleClick}
          />
        )}

        <MapLegend loading={dataLoading} />

        {hoveredState && <StateTooltip state={hoveredState} position={tooltipPos} />}
      </div>
    </div>
  )
}

export default USMap

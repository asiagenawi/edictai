import { createPortal } from 'react-dom'
import { STATUS_COLORS, STATUS_DESCRIPTIONS } from '../../utils/colors'

const WIDTH = 220
const OFFSET = 14

/**
 * Rendered through a portal so it escapes the map's stacking and overflow, and
 * flipped near the viewport edge so it no longer clips against the right side.
 */
function StateTooltip({ state, position }) {
  const status = state.policy_status || 'none'

  const flipX = position.x + OFFSET + WIDTH > window.innerWidth
  const left = flipX ? position.x - OFFSET - WIDTH : position.x + OFFSET
  const top = Math.min(position.y - 10, window.innerHeight - 110)

  return createPortal(
    <div className="state-tooltip" style={{ left, top, width: WIDTH }} role="status" aria-live="polite">
      <span className="tooltip-name">{state.name}</span>
      <span className="tooltip-status">
        <span className="tooltip-status-dot" style={{ backgroundColor: STATUS_COLORS[status] }} />
        {STATUS_DESCRIPTIONS[status]}
      </span>
      <span className="tooltip-count">
        {state.policy_count > 0
          ? `${state.policy_count} ${state.policy_count === 1 ? 'policy' : 'policies'} — click to view`
          : 'Nothing on record yet'}
      </span>
    </div>,
    document.body,
  )
}

export default StateTooltip

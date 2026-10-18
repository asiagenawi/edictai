import { useEffect, useRef, useCallback } from 'react'
import { STATUS_COLORS, STATUS_ORDER, STATUS_LABELS } from '../../utils/colors'
import { markGuideSeen } from '../../utils/guide'

/**
 * A short "how to use this" panel, shown once on a first visit.
 *
 * Every step below describes something the interface actually does, so this
 * needs updating if those controls change -- a guide that describes a button
 * that is not there is worse than no guide.
 */
const STEPS = [
  {
    title: 'Read the colours',
    body: 'Each state is shaded by the most significant action it has taken. Darker means more binding policy is in force; grey means nothing was found.',
    swatches: true,
  },
  {
    title: 'Click a state',
    body: 'Its bills, executive orders, and department guidance open in a panel on the right, each linked to the original source.',
  },
  {
    title: 'Search for a state',
    body: 'Use the search box in the header to jump straight to one — easier than hunting for the small states in the Northeast.',
  },
  {
    title: 'Ask a question',
    body: 'Ask opens an assistant that answers from the tracked policies — useful for comparing states or spotting trends.',
  },
]

function WelcomeGuide({ open, onClose }) {
  const closeRef = useRef(null)
  const returnFocusRef = useRef(null)

  const dismiss = useCallback(() => {
    markGuideSeen()
    if (returnFocusRef.current instanceof HTMLElement) {
      returnFocusRef.current.focus()
    }
    onClose()
  }, [onClose])

  // Focus moves into the dialog on open and returns to its trigger on close.
  useEffect(() => {
    if (!open) return
    returnFocusRef.current = document.activeElement
    closeRef.current?.focus()
  }, [open])

  // Captured, so Escape closes the guide rather than the drawer behind it.
  useEffect(() => {
    if (!open) return
    function onKeyDown(e) {
      if (e.key !== 'Escape') return
      e.stopPropagation()
      dismiss()
    }
    window.addEventListener('keydown', onKeyDown, true)
    return () => window.removeEventListener('keydown', onKeyDown, true)
  }, [open, dismiss])

  if (!open) return null

  return (
    <div className="guide-scrim" onClick={dismiss}>
      <div
        className="guide"
        role="dialog"
        aria-modal="true"
        aria-labelledby="guide-title"
        onClick={e => e.stopPropagation()}
      >
        <button
          ref={closeRef}
          className="guide-close"
          onClick={dismiss}
          aria-label="Close and start exploring"
        >
          <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
            <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
        </button>

        <p className="guide-eyebrow">Welcome to Edictai</p>
        <h2 className="guide-title" id="guide-title">
          Who’s regulating AI in the classroom?
        </h2>
        <p className="guide-intro">
          A tracker of every state law, executive order, and education-department
          guidance document on AI in schools. Here’s how to use it.
        </p>

        <ol className="guide-steps">
          {STEPS.map((step, i) => (
            <li key={step.title} className="guide-step">
              <span className="guide-step-num" aria-hidden="true">{i + 1}</span>
              <div className="guide-step-body">
                <h3 className="guide-step-title">{step.title}</h3>
                <p className="guide-step-text">{step.body}</p>
                {step.swatches && (
                  <div className="guide-scale">
                    {STATUS_ORDER.map(status => (
                      <span key={status} className="guide-scale-item">
                        <span
                          className="guide-swatch"
                          style={{ backgroundColor: STATUS_COLORS[status] }}
                        />
                        <span className="guide-scale-label">{STATUS_LABELS[status]}</span>
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </li>
          ))}
        </ol>

        <div className="guide-foot">
          <button className="guide-cta" onClick={dismiss}>Start exploring</button>
          <span className="guide-note">You can reopen this from the header.</span>
        </div>
      </div>
    </div>
  )
}

export default WelcomeGuide

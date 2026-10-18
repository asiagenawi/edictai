import { useState, useRef, useEffect } from 'react'
import { downloadCSV } from '../../utils/exportData'
import { buildCitation } from '../../utils/citation'

/**
 * Take the data away, and cite it.
 *
 * Both are standard on serious data tools and neither existed here: the
 * download gives exactly the rows currently on screen, and the citation carries
 * both the curation date and the retrieval date, because a page that changes
 * cannot be cited honestly by either one alone.
 */
function DataActions({ policies, dataUpdated, filtered }) {
  const [showCite, setShowCite] = useState(false)
  const [copied, setCopied] = useState(false)
  const rootRef = useRef(null)

  const citation = buildCitation(dataUpdated)

  useEffect(() => {
    if (!showCite) return
    function onClickOutside(e) {
      if (rootRef.current && !rootRef.current.contains(e.target)) setShowCite(false)
    }
    function onKeyDown(e) {
      if (e.key === 'Escape') setShowCite(false)
    }
    document.addEventListener('mousedown', onClickOutside)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onClickOutside)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [showCite])

  async function copyCitation() {
    try {
      await navigator.clipboard.writeText(citation)
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch {
      // Clipboard blocked (insecure context or denied): the text is on screen
      // and selectable, so this is a convenience rather than the only route.
    }
  }

  return (
    <div className="data-actions" ref={rootRef}>
      <button
        className="data-action"
        onClick={() => downloadCSV(
          policies,
          filtered ? 'edictai-policies-filtered.csv' : 'edictai-policies.csv',
        )}
        disabled={!policies.length}
      >
        <svg viewBox="0 0 14 14" width="12" height="12" aria-hidden="true">
          <path d="M7 1v8M4 6.5 7 9.5l3-3M2 11.5h10" fill="none" stroke="currentColor"
            strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        Download CSV
        {filtered && <span className="data-action-note">({policies.length})</span>}
      </button>

      <button
        className="data-action"
        onClick={() => setShowCite(s => !s)}
        aria-expanded={showCite}
      >
        <svg viewBox="0 0 14 14" width="12" height="12" aria-hidden="true">
          <path d="M5.4 3.2c-1.6.6-2.6 2-2.6 3.8v3h3.4V7H4.4c0-1.1.5-1.9 1.6-2.3l-.6-1.5ZM11 3.2c-1.6.6-2.6 2-2.6 3.8v3h3.4V7H10c0-1.1.5-1.9 1.6-2.3L11 3.2Z"
            fill="currentColor" />
        </svg>
        Cite this
      </button>

      {showCite && (
        <div className="cite-pop" role="dialog" aria-label="How to cite">
          <p className="cite-label">Suggested citation</p>
          <p className="cite-text">{citation}</p>
          <div className="cite-actions">
            <button className="cite-copy" onClick={copyCitation}>
              {copied ? 'Copied' : 'Copy citation'}
            </button>
            <span className="cite-note">
              Includes both the data date and today’s retrieval date.
            </span>
          </div>
        </div>
      )}
    </div>
  )
}

export default DataActions

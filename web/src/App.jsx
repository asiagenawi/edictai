import { useState, useEffect, useCallback, lazy, Suspense } from 'react'
import Header from './components/Layout/Header'
import USMap from './components/Map/USMap'
import GlobalChat from './components/GlobalChat'
import StatePolicyPanel from './components/PolicyPanel/StatePolicyPanel'
import ResizeHandle from './components/Layout/ResizeHandle'
import WelcomeGuide from './components/Layout/WelcomeGuide'
import { useResizablePanel } from './hooks/useResizablePanel'
import { useMediaQuery } from './hooks/useMediaQuery'
import { useSnapshot } from './hooks/useSnapshot'
import { warmBackend } from './utils/api'
import { hasSeenGuide } from './utils/guide'
import './App.css'

// Trends pulls in recharts, which has no business being in the bundle that
// paints the map.
const TrendsView = lazy(() => import('./components/Trends/TrendsView'))
const PolicyTable = lazy(() => import('./components/Table/PolicyTable'))

function App() {
  const isMobile = useMediaQuery('(max-width: 900px)')
  const snapshot = useSnapshot()

  const [view, setView] = useState('map')
  const [selectedState, setSelectedState] = useState(null)
  const [chatOpen, setChatOpen] = useState(false)
  // Shown once on a first visit; reopenable from the header afterwards.
  const [guideOpen, setGuideOpen] = useState(() => !hasSeenGuide())

  const policyResize = useResizablePanel({
    defaultWidth: 400,
    minWidth: 300,
    maxWidth: 620,
    side: 'right',
    disabled: isMobile,
  })

  const chatResize = useResizablePanel({
    defaultWidth: 420,
    minWidth: 320,
    maxWidth: 650,
    side: 'right',
    disabled: isMobile,
  })

  // The map needs no backend, but /ask does and the free tier sleeps. Wake it
  // now so the dyno is up by the time anyone opens the chat.
  useEffect(() => { warmBackend() }, [])

  // Escape closes whatever is open, innermost first.
  useEffect(() => {
    function onKeyDown(e) {
      if (e.key !== 'Escape') return
      if (chatOpen) setChatOpen(false)
      else if (selectedState) setSelectedState(null)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [chatOpen, selectedState])

  const handleSelectState = useCallback(state => {
    setView('map')
    setSelectedState(prev => (prev?.code === state?.code ? null : state))
  }, [])

  const drawersOpen = Boolean(selectedState) || chatOpen

  return (
    <div className="app">
      <Header
        view={view}
        onViewChange={setView}
        chatOpen={chatOpen}
        onToggleChat={() => setChatOpen(o => !o)}
        snapshot={snapshot}
        onSelectState={handleSelectState}
        onOpenGuide={() => setGuideOpen(true)}
      />

      <div className={`app-body${drawersOpen ? ' app-body--drawers' : ''}`}>
        <main className="stage" id="main-content">
          {view === 'map' ? (
            <USMap
              snapshot={snapshot}
              selectedState={selectedState}
              onSelectState={handleSelectState}
            />
          ) : view === 'table' ? (
            <Suspense fallback={<div className="view-loading">Loading policies…</div>}>
              <PolicyTable snapshot={snapshot} onSelectState={handleSelectState} />
            </Suspense>
          ) : (
            <Suspense fallback={<div className="view-loading">Loading trends…</div>}>
              <TrendsView snapshot={snapshot} onSelectState={handleSelectState} />
            </Suspense>
          )}
        </main>

        {selectedState && (
          <>
            {!isMobile && <ResizeHandle onMouseDown={policyResize.handleProps.onMouseDown} />}
            <StatePolicyPanel
              state={selectedState}
              states={snapshot.states}
              policies={snapshot.policiesByState[selectedState.code] || []}
              onClose={() => setSelectedState(null)}
              onSelectState={setSelectedState}
              style={policyResize.width != null ? { width: policyResize.width } : undefined}
            />
          </>
        )}

        {chatOpen && (
          <>
            {!isMobile && <ResizeHandle onMouseDown={chatResize.handleProps.onMouseDown} />}
            <GlobalChat
              onClose={() => setChatOpen(false)}
              style={chatResize.width != null ? { width: chatResize.width } : undefined}
            />
          </>
        )}
      </div>

      <WelcomeGuide open={guideOpen} onClose={() => setGuideOpen(false)} />
    </div>
  )
}

export default App

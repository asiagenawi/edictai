import { useState, useRef, useCallback, useEffect } from 'react'

export function useResizablePanel({ defaultWidth, minWidth, maxWidth, side = 'right', disabled = false }) {
  const [width, setWidth] = useState(defaultWidth)
  // Mirrored in state as well as the ref: the ref drives the mousemove handler,
  // the state is what render is allowed to read.
  const [isResizing, setIsResizing] = useState(false)
  const dragRef = useRef(null)

  const onMouseDown = useCallback(e => {
    if (disabled) return
    e.preventDefault()
    dragRef.current = { startX: e.clientX, startWidth: width }
    setIsResizing(true)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [disabled, width])

  useEffect(() => {
    function onMouseMove(e) {
      if (!dragRef.current) return
      const delta = e.clientX - dragRef.current.startX
      const newWidth = side === 'right'
        ? dragRef.current.startWidth - delta
        : dragRef.current.startWidth + delta
      setWidth(Math.min(maxWidth, Math.max(minWidth, newWidth)))
    }

    function onMouseUp() {
      if (!dragRef.current) return
      dragRef.current = null
      setIsResizing(false)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }

    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)
    return () => {
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup', onMouseUp)
    }
  }, [minWidth, maxWidth, side])

  return {
    width: disabled ? undefined : width,
    isResizing: !disabled && isResizing,
    handleProps: { onMouseDown },
  }
}

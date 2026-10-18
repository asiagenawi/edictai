function ResizeHandle({ onMouseDown }) {
  return (
    <div className="resize-handle" onMouseDown={onMouseDown}>
      <div className="resize-handle-bar" />
    </div>
  )
}

export default ResizeHandle

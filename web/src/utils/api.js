const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:5001/api'

export async function fetchJSON(endpoint) {
  const res = await fetch(`${API_BASE}${endpoint}`)
  if (!res.ok) {
    throw new Error(`API error: ${res.status}`)
  }
  return res.json()
}

export async function postJSON(endpoint, body) {
  const res = await fetch(`${API_BASE}${endpoint}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    throw new Error(`API error: ${res.status}`)
  }
  return res.json()
}

/**
 * Wake the API in the background.
 *
 * The map no longer needs the backend at all, but /ask still does, and the free
 * Render tier sleeps after 15 minutes idle. Pinging on page load means the dyno
 * is usually up by the time anyone opens the chat and types a question, instead
 * of that 30-60s cold start landing on the first message.
 *
 * Resolves to true once the API answers, so the chat can tell the difference
 * between "still waking up" and "slow model response".
 */
let warmPromise = null

export function warmBackend() {
  if (!warmPromise) {
    warmPromise = fetch(`${API_BASE}/health`)
      .then(res => res.ok)
      .catch(() => false)
  }
  return warmPromise
}

export function isBackendWarm() {
  return warmPromise !== null
}

/**
 * Stream an answer from /ask/stream, yielding text deltas as they arrive.
 *
 * Falls back to the non-streaming /ask endpoint if streaming is unavailable, so
 * an older deployed backend keeps working.
 */
export async function streamAsk(question, { onDelta, signal }) {
  let res
  try {
    res = await fetch(`${API_BASE}/ask/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
      signal,
    })
  } catch (err) {
    if (err.name === 'AbortError') throw err
    res = null
  }

  if (!res || !res.ok || !res.body) {
    const data = await postJSON('/ask', { question })
    onDelta(data.answer)
    return data.answer
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let answer = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // SSE frames are separated by a blank line.
    let split
    while ((split = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, split)
      buffer = buffer.slice(split + 2)
      for (const line of frame.split('\n')) {
        if (!line.startsWith('data:')) continue
        const payload = JSON.parse(line.slice(5).trim())
        if (payload.error) throw new Error(payload.error)
        if (payload.text) {
          answer += payload.text
          onDelta(answer)
        }
      }
    }
  }

  return answer
}

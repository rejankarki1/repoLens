/** One parsed server-sent event. `data` is the raw (single- or multi-line) payload text. */
export interface SseEvent {
  event: string
  data: string
}

/** Parses one event block (the text between blank lines). Returns null for comments and empty blocks. */
export function parseSseBlock(block: string): SseEvent | null {
  let event = 'message'
  const data: string[] = []
  for (const line of block.split(/\r?\n/)) {
    if (!line || line.startsWith(':')) continue
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    const value = colon === -1 ? '' : line.slice(colon + 1).replace(/^ /, '')
    if (field === 'event') event = value
    else if (field === 'data') data.push(value)
  }
  return data.length ? { event, data: data.join('\n') } : null
}

/** Reads a response body as server-sent events, handling chunk boundaries that split an event. */
export async function* readSse(body: ReadableStream<Uint8Array>): AsyncGenerator<SseEvent> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    for (;;) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      const blocks = buffer.split(/\r?\n\r?\n/)
      buffer = blocks.pop() ?? '' // an event cut off by the end of the stream is incomplete and discarded
      for (const block of blocks) {
        const event = parseSseBlock(block)
        if (event) yield event
      }
      if (done) return
    }
  } finally {
    reader.cancel().catch(() => {})
  }
}

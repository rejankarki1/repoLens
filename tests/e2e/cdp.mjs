// A tiny Chrome DevTools Protocol driver with real mouse and keyboard input. No dependencies (Node 22 has fetch and WebSocket).
export async function connect(port) {
  const tabs = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
  const ws = new WebSocket(tabs.find((t) => t.type === 'page').webSocketDebuggerUrl)
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject })
  let id = 0
  const pending = new Map()
  const problems = []
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data)
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); return }
    if (msg.method === 'Runtime.exceptionThrown') problems.push('exception: ' + (msg.params.exceptionDetails.exception?.description ?? msg.params.exceptionDetails.text))
    if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') problems.push('console.error: ' + msg.params.args.map((a) => a.value ?? a.description ?? '').join(' ').slice(0, 200))
  }
  const send = (method, params = {}) => new Promise((resolve) => { const i = ++id; pending.set(i, resolve); ws.send(JSON.stringify({ id: i, method, params })) })
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
  const d = {
    problems, sleep, send,
    close: () => ws.close(),
    async goto(url, wait = 1500) { await send('Page.navigate', { url }); await sleep(wait) },
    async ev(expression) {
      const r = (await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })).result
      if (r.exceptionDetails) throw new Error('eval failed: ' + (r.exceptionDetails.exception?.description ?? r.exceptionDetails.text))
      return r.result.value
    },
    async waitFor(expression, ms = 20000) {
      const t = Date.now()
      while (Date.now() - t < ms) { try { if (await d.ev(expression)) return true } catch { /* page is navigating */ } await sleep(150) }
      return false
    },
    async centre(selectorOrExpr) {
      const get = selectorOrExpr.startsWith('(') ? selectorOrExpr : `document.querySelector(${JSON.stringify(selectorOrExpr)})`
      const r = await d.ev(`(()=>{const e=${get};if(!e)return null;e.scrollIntoView({block:'nearest'});const b=e.getBoundingClientRect();return [b.x+b.width/2,b.y+b.height/2]})()`)
      if (!r) throw new Error('element not found: ' + selectorOrExpr)
      return r
    },
    async click(x, y) {
      await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y })
      await send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', buttons: 1, clickCount: 1 })
      await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', buttons: 0, clickCount: 1 })
    },
    async clickOn(selectorOrExpr) { const [x, y] = await d.centre(selectorOrExpr); await d.click(x, y) },
    async type(text) { await send('Input.insertText', { text }) },
    async enter() {
      await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, text: '\r', unmodifiedText: '\r' })
      await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 })
    },
    async cookies(url) { return (await send('Network.getCookies', { urls: [url] })).result.cookies },
  }
  await send('Runtime.enable'); await send('Page.enable'); await send('Network.enable')
  return d
}

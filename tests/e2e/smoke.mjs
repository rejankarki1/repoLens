// Browser smoke test: visitor, guest limits, paste-URL -> sign in -> automatic import, isolation between users, sign out.
// Runs against a stack started by `python -m tests.e2e.run_smoke`, with a FAKE GitHub (not real OAuth).
import { connect } from './cdp.mjs'

const { WEB, API, GH, CDP } = Object.fromEntries(['WEB', 'API', 'GH', 'CDP'].map((k) => [k, process.env['E2E_' + k]]))
const results = []
const check = (name, ok, detail = '') => { results.push({ name, ok: !!ok }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : '  ' + detail}`) }
const control = (body) => fetch(`${GH}/_control`, { method: 'POST', body: JSON.stringify(body) })
const text = (d) => d.ev('document.body.innerText')

const d = await connect(CDP)
async function paste(url) {
  await d.goto(`${WEB}/dashboard`, 2500)
  await d.clickOn('#repo-url'); await d.type(url); await d.enter(); await d.sleep(1200)
}
async function signInAs(user) {
  await control({ user, mode: 'approve' })
  await d.goto(`${API}/auth/github/login?next=%2Frepositories`, 0)
  await d.waitFor(`location.origin === '${WEB}'`); await d.sleep(2000)
}
async function waitIngested() {
  return d.waitFor(`/Ingested/.test(document.body.innerText) && /Files\\s+\\d+/.test(document.body.innerText)`, 60000)
}
async function signOut() {
  await d.clickOn('button[aria-label^="Account menu"]'); await d.sleep(500)
  await d.clickOn(`(()=>[...document.querySelectorAll('[role=menuitem]')].find(e=>/Sign out/.test(e.innerText)))()`)
}
const links = (d) => d.ev(`[...document.querySelectorAll('a')].map(a=>a.getAttribute('href'))`)

try {
  // 1. A visitor: guest sidebar, nothing imported, sign-in offered.
  await d.goto(`${WEB}/repositories`, 2500)
  let page = await text(d)
  check('visitor sees the guest sidebar with Continue with GitHub', /Browsing the demo/.test(page) && /Continue with GitHub/.test(page))
  check('visitor has no repositories yet (empty showcase)', /No demo repositories yet/.test(page))

  // 2. Paste a URL as a visitor: sign-in prompt, nothing is imported.
  await paste('https://github.com/octo/demo')
  page = await text(d)
  check('pasting a repository as a visitor asks to sign in and names the repository', /Sign in to analyze octo\/demo/.test(page))
  check('no import was started for the visitor', (await (await fetch(`${API}/repositories`)).json()).length === 0)

  // 3. Real click on Continue with GitHub (the prompt's button): OAuth with PKCE against the fake GitHub, then automatic import.
  await d.clickOn(`(()=>[...document.querySelectorAll('[role=status] a')].find(a=>/Continue with GitHub/.test(a.innerText) && a.href.includes('import')))()`)
  check('after sign-in the pasted repository opens its workspace', await d.waitFor(`/^\\/repos\\/\\d+$/.test(location.pathname)`, 30000), await d.ev('location.href'))
  const repoPath = await d.ev('location.pathname')
  check('the repository is downloaded and indexed by the worker', await waitIngested(), (await text(d)).slice(0, 200))
  page = await text(d)
  check('the sidebar shows the signed-in GitHub user, not a demo persona', /Alice Example/.test(page) && /@alice/.test(page) && !/Alex Doe/.test(page))
  check('the session cookie is HttpOnly and invisible to page scripts', (await d.ev('document.cookie')) === '' && (await d.cookies(API)).some((c) => c.name === 'rl_session' && c.httpOnly && c.sameSite === 'Lax'))
  check('no tokens are kept in web storage', (await d.ev('JSON.stringify([Object.keys(localStorage), Object.keys(sessionStorage)])')) === '[[],[]]')
  check('the pasted URL is no longer in the address bar', (await d.ev('location.search')) === '')
  await d.goto(`${WEB}${repoPath}?tab=ask`, 2500)
  check('a member sees the Ask AI composer', await d.ev(`!!document.querySelector('textarea[aria-label="Your question"], input[aria-label="Your question"]')`))

  // 4. Isolation: Alice also analyses a repository that is NOT in the showcase; a second user cannot open it.
  await paste('https://github.com/octo/second')
  check('a signed-in member can paste another repository and gets its workspace', await d.waitFor(`/^\\/repos\\/\\d+$/.test(location.pathname) && location.pathname !== '${repoPath}'`, 30000))
  const privatePath = await d.ev('location.pathname')
  check('the second repository is ingested too', await waitIngested())
  await signOut(); await d.sleep(1500)
  check('signing out returns to the guest view', /Browsing the demo/.test(await text(d)))
  check('the session cookie is gone after sign out', (await d.cookies(API)).every((c) => c.name !== 'rl_session'))
  await signInAs('bob')
  await d.goto(`${WEB}${privatePath}`, 2500)
  check("another user cannot open Alice's private repository", /Repository not found/.test(await text(d)))
  check("another user's own repository list is empty (the showcase is not their workspace)", (await d.ev(`fetch('${API}/repositories',{credentials:'include'}).then(r=>r.json())`)).length === 0)

  // 5. Cancelling on GitHub changes nothing.
  await signOut(); await d.sleep(1200)
  await control({ mode: 'deny' })
  await d.goto(`${API}/auth/github/login?next=%2Frepositories`, 0)
  await d.waitFor(`location.origin === '${WEB}'`); await d.sleep(2000)
  check('cancelling GitHub sign-in shows a clear message and stays signed out', /sign-in was cancelled/i.test(await text(d)) && /Browsing the demo/.test(await text(d)))
  await control({ mode: 'approve' })

  // 6. The guest showcase: add the repository to the allow-list is done by configuration (E2E_SHOWCASE), so a visitor can browse it read-only.
  await d.goto(`${WEB}/repositories`, 2500)
  page = await text(d)
  check('the showcase repository is listed for visitors', /octo\/demo/.test(page) && /browsing the demo/i.test(page))
  await d.goto(`${WEB}${repoPath}`, 2500)
  page = await text(d)
  check('a visitor can open the showcase read-only (badge, no Re-analyze)', /Showcase · read-only/.test(page) && !/Re-analyze/.test(page))
  await d.goto(`${WEB}${repoPath}?tab=ask`, 2500)
  check('a visitor cannot use Ask AI', /Ask AI needs your own workspace/.test(await text(d)))
  await d.goto(`${WEB}${repoPath}?tab=code&file=app%2Fauth.py`, 3000)
  check('a visitor can read source files', /hash_password/.test(await text(d)))
  const id = repoPath.split('/').pop()
  const guestApi = await d.ev(`(async()=>{const c={credentials:'include'};const j={'Content-Type':'application/json'};const s=async(p,o)=> (await fetch('${API}'+p,{...c,...o})).status; return {
    import: await s('/repositories',{method:'POST',headers:j,body:JSON.stringify({github_url:'https://github.com/octo/second'})}),
    ingest: await s('/repositories/${id}/ingestions',{method:'POST'}), analyze: await s('/repositories/${id}/architecture',{method:'POST'}),
    chats: await s('/repositories/${id}/conversations'), search: await s('/repositories/${id}/search?q=hash'), files: await s('/repositories/${id}/files') }})()`)
  check('a visitor cannot import, re-ingest, analyze, search or read conversations (401), but can read files',
    guestApi.import === 401 && guestApi.ingest === 401 && guestApi.analyze === 401 && guestApi.chats === 401 && guestApi.search === 401 && guestApi.files === 200, JSON.stringify(guestApi))

  // 7. Removing repositories: a confirmation, clear feedback, GitHub untouched; a showcase repository stays browsable.
  await signInAs('alice')
  await d.goto(`${WEB}/repositories`, 2500)
  await d.waitFor(`!!document.querySelector('button[aria-label="Remove octo/second"]')`, 20000)
  page = await text(d)
  check('the repository list offers Open, Re-analyze and Remove', /octo\/second/.test(page) && /Re-analyze/.test(page) && (await d.ev(`!!document.querySelector('button[aria-label="Remove octo/second"]')`)))
  const dialog = () => d.ev(`document.querySelector('[role=alertdialog]')?.innerText ?? ''`)
  const press = (label) => d.clickOn(`(()=>[...document.querySelectorAll('[role=alertdialog] button')].find(b=>/^${label}$/.test(b.innerText.trim())))()`)
  await d.clickOn('button[aria-label="Remove octo/second"]'); await d.sleep(700)
  check('removal asks for confirmation and says GitHub is not affected', /Remove octo\/second\?/.test(await dialog()) && /not affected/.test(await dialog()))
  await press('Cancel'); await d.sleep(700)
  check('cancelling removes nothing', /octo\/second/.test(await text(d)) && (await d.ev(`fetch('${API}/repositories/${privatePath.split('/').pop()}',{credentials:'include'}).then(r=>r.status)`)) === 200)
  await d.clickOn('button[aria-label="Remove octo/second"]'); await d.sleep(700)
  await press('Remove repository'); await d.sleep(1500)
  check('confirming removes it and reports success', /Removed octo\/second from your repositories/.test(await text(d)) && !(await d.ev(`!!document.querySelector('button[aria-label="Remove octo/second"]')`)))
  check('its stored data is gone (it was in nobody else\'s workspace)', (await d.ev(`fetch('${API}/repositories/${privatePath.split('/').pop()}',{credentials:'include'}).then(r=>r.status)`)) === 404)
  await d.clickOn('button[aria-label="Remove octo/demo"]'); await d.sleep(700)
  await press('Remove repository'); await d.sleep(1500)
  await signOut(); await d.sleep(1200)
  check('a removed showcase repository is still open to visitors', (await d.ev(`fetch('${API}/repositories/${id}/files').then(r=>r.status)`)) === 200)

  check('no uncaught page errors during the whole run', d.problems.length === 0, d.problems.slice(0, 3).join(' | '))
} catch (error) {
  check('the smoke test ran to completion', false, String(error.stack ?? error).split('\n').slice(0, 4).join(' | '))
} finally {
  d.close()
}
const failed = results.filter((r) => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} checks passed`)
process.exit(failed.length ? 1 : 0)

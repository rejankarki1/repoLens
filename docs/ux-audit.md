# RepoLens desktop UX audit — polish/ux-final

Baseline: `89bf316` (deployed production merge). Screens inspected in a local preview wired to a
clone of real data (two demo repositories, one failed repository, saved conversations), at
1440×900 and 1280×800, dark and light, as three users: guest, new signed-in user, returning user.
Screenshots: `scratchpad/ux/before/` and `after*/` (session scratchpad; copied nowhere else).

## Issues found (severity · screen · state)

| # | Screen | Issue | Severity | Status |
|---|--------|-------|----------|--------|
| 1 | Dashboard | A failed card offers no action; recovery only via workspace or Repositories page | High | Fixed (1) |
| 2 | Dashboard (new user) | Empty state says what to do but not what happens next (ingestion? ready when?) | High | Fixed (1) |
| 3 | Everywhere | "Keyword search", "Ingested", "Semantic index ready" unexplained jargon | High | Fixed (1) |
| 4 | Sidebar + dashboard | "Recent" is actually import order; returning users cannot find the repository they just used | Medium | Fixed (1) |
| 5 | Sign-in | Two labels for one action ("Continue with GitHub" vs "Sign in with GitHub"); "Showcase" vs "Demo" naming split | Medium | Fixed (1) |
| 6 | Workspace | Failed repository page: run-on sentence "…download size Use Re-analyze above…", no action below the fold | Medium | Fixed (2) |
| 7 | Ask AI | Empty/streaming states fine, but "Semantic search is not configured" note is operator-speak shown to end users | Medium | Fixed (2) |
| 8 | Repositories list | Rows lack the index badge the dashboard has; inconsistent info density | Low | Fixed (2) |
| 9 | Landing 1280 | Hero screenshot and section spacing hold up; no blocker | — | No change |
| 10 | Architecture | Suspected minimap/panel overlap at 1280 | — | Not reproduced: the minimap is a floating overlay inside the canvas by design; the details panel sits beside it |
| 11 | Architecture | "1 generation warning" details is easy to miss but harmless | Low | No change |
| 12 | Guest Ask | Sidebar sign-in box appeared clipped at 900px height | Low | Resolved: the overlap was the Next dev-tools badge in the local preview plus long copy; copy shortened in batch 1, verified clean after |
| 13 | Code Explorer | Opening a file keeps tree selection invisible when tree scrolled; minor | Low | No change |
| 14 | Overview | Solid baseline (stats, languages, architecture summary); no change needed | — | No change |

Production citation-format bug (Groq writing `【S4†…】` markers) is backend scope, recorded in the
deployment report, deliberately untouched here.

## Batches

### Batch 1 — dashboard, statuses, recents, naming (commit: see git log)
- Failed/pending dashboard cards now carry "Try again" / "Start ingestion" with inline errors.
- While ingesting, cards explain the state in a sentence instead of a bare metadata row.
- New-user dashboard: a three-step "what happens next" panel replacing the bare empty box.
- "What do the statuses mean?" collapsible on the dashboard; every status/index badge now has a tooltip.
- Recents are now truly recent: opening a member repository records it (localStorage; cleared on
  sign-out), sidebar and dashboard sort by it; unit-tested pure logic in `lib/recent-repos.ts`.
- One sign-in label everywhere ("Sign in with GitHub"); "Demo · read-only" matches the sidebar's "Demo".
- Dashboard link "Manage repositories" → "All repositories" (it is a list, management is an action there).

Tests: new `lib/recent-repos.test.ts` (5), extended index-badge tests; full unit suite, lint, tsc.

| 15 | Dashboard cards (390px, long names) | Grid column grows past the viewport (grid items default to min-width auto), pushing every status badge off-screen | High | Fixed (3) |
| 16 | Ask AI chat list | Checked Clear history / Delete for missing confirmation — both already confirm inline | — | No change |

### Batch 2 — workspace failure state, Ask AI wording, repositories list (commit 0974af9)
- "Analysis failed" became "Ingestion failed" with a period after the reason and a real **Try again**
  button in place of "Use Re-analyze above to try again."
- The notes above the Ask AI composer now describe matching behaviour in user terms ("Answers match
  on exact words and names…") instead of configuration state ("Semantic search is not configured").
- Repositories rows show the same search-readiness badge as dashboard cards (one non-polling fetch per row).

Tests: unit suite (120), lint, tsc; after-shots `after2/`.

### Batch 3 — long-name card overflow, lint/smoke upkeep (commit: see git log)
- `min-w-0` on the repository card so grid columns cannot outgrow the viewport; at 390px with a
  long repository name every status badge was rendered off-screen (x=423 on a 390px viewport).
  Verified by DOM measurement before/after, not only visually.
- eslint now ignores `.next-*/**` (the isolated smoke/build dirs introduced earlier produced 28
  errors on generated files); the smoke test's web-storage check now pins the two legitimate
  non-secret keys (theme, recently-opened ids) and asserts the recents entry holds only ids+times.

Final verification: unit 120/120, lint clean, tsc clean, production build compiled, browser smoke
31/31 (sign-in flow, guest showcase, imports, isolation, removal; against the isolated build dir).

## Journey re-review (after)
- **Guest**: landing explains the product, demo and 3-question limit in the first screen; demo pages
  carry quota, sign-in action and read-only badge; wording uses one name ("Demo") and one sign-in label.
- **New user**: empty dashboard now teaches the three steps and links back to the import box; every
  status has a tooltip and the status guide explains the vocabulary; failed/pending cards carry the
  recovery action inline; no dead ends found at 1440/1280/390.
- **Returning user**: sidebar and dashboard order by last opened (cleared on sign-out); Repositories
  keeps import order for scanning, with search; workspace tabs unchanged.

## Remaining (not addressed, by design or out of scope)
- Production citation-format bug (backend, recorded in the deployment report).
- Architecture "1 generation warning" stays quiet by design; minimap placement unchanged.
- Code Explorer tree does not auto-scroll to a file opened via citation link (minor; low risk to touch).
- Landing screenshots are dark-theme captures framed on both themes (deliberate).


## Follow-up pass — guest/member dashboard separation (commit: see git log)

Guest and signed-in `/dashboard` were the same page with a label swapped underneath ("Analyze a
repository" shown even to visitors who cannot analyze anything without signing in; "Recent
repositories" shown even though nothing had ever been opened). Split into two real screens:

- **New `GuestDashboard`** (`components/guest-dashboard.tsx`): "Demo repositories" heading leads,
  with the two showcase repositories as full `DemoRepositoryCard`s (description, stack, Demo badge,
  Ask AI primary). "Import your own repository" is a secondary, visually separated section below it
  with a **Sign in with GitHub** button in its own heading (shown up front, not only after a paste
  fails) plus the existing paste form, unchanged, so the sign-in-after-paste resume flow still works.
- **New `MemberDashboard`** (`components/member-dashboard.tsx`): the previous content, unchanged,
  heading fixed to always say "Recent repositories" (previously the same literal string was used for
  guests too, who have no "recent" anything).
- **New `DashboardView`** (`components/dashboard-view.tsx`): picks between them once `useAuth()`
  resolves; `page.tsx` is back to a thin server component with just the route's metadata.
- **`RepositoryList` simplified**: it is now only ever rendered for a signed-in member (the guest
  branch, `guestNotice`, and the "no demo repositories" copy it carried are gone — dead code once
  `DashboardView` stopped routing guests through it).
- **New shared `DemoRepositoryCard`** (`components/demo-repository-card.tsx`) and
  **`lib/demo-repo-descriptions.ts`**: the repository description/stack text and the card that shows
  it now live in one place, used by both the landing page and the guest dashboard, so the two
  describe the showcase the same way. `components/landing/demo-repositories.tsx` now delegates to it
  instead of carrying its own copy of the same card.
- **`RepositoryCard` (member cards)**: reordered to put search-readiness (`IndexBadge`) and the
  ingested time first; branch and commit moved to a de-emphasized, right-aligned, muted group at the
  end of the row. Added a quiet "opened 3h ago" indicator (History icon) next to the status badge,
  shown only for a repository this browser has actually opened — makes "recent access" visible on the
  card itself, not just the sort order.

Verified: guest dashboard, member dashboard (with a genuinely-recent repository showing its "opened
Xago" tag), new-user empty state, and Repositories (regression) at 1440 dark/light and 390 dark — no
overflow, no visual regression on unrelated screens. Full suite: 120/120 unit tests, lint clean, tsc
clean, production build compiled, browser smoke 31/31.

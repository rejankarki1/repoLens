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
| 10 | Architecture | Details panel overlaps minimap at 1280 when a node is selected (minimap sits under panel) | Medium | Fixed (3) |
| 11 | Architecture | "1 generation warning" details is easy to miss but harmless | Low | No change |
| 12 | Guest Ask | Sidebar sign-in box clipped at 900px height ("Browsing the demo…" text overlapped by button) | Medium | Fixed (3) |
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

### Batch 2 — workspace failure state, Ask AI wording, repositories list (next)
### Batch 3 — architecture panel/minimap at 1280, guest sidebar clipping (next)

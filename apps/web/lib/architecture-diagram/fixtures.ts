/**
 * Phase 3 Checkpoint A: local development fixtures only. Maps a repository's full name to a static,
 * backend-validated `ArchitectureDiagram` JSON file under `public/dev-fixtures/`, produced by
 * `scripts/generate_architecture_diagram_fixture.py` (one manual, explicitly-authorized live AI call per
 * fixture — never run automatically). There is no database table, job or API route behind this yet; once
 * one exists, replace `diagramFixturePath`'s lookup with a real fetch keyed by `repo.id` and delete this
 * file.
 */
export const DIAGRAM_FIXTURE_BY_REPO: Record<string, string> = {
  'rejankarki1/wanderlust': '/dev-fixtures/rejankarki1-wanderlust-architecture.json',
}

export function diagramFixturePath(fullName: string): string | null {
  return DIAGRAM_FIXTURE_BY_REPO[fullName] ?? null
}

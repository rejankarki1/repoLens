import type { Metadata } from 'next'
import { Settings } from 'lucide-react'

export const metadata: Metadata = { title: 'Settings' }

export default function SettingsPage() {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-8 px-4 py-10 sm:px-8">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground">Workspace, GitHub connection, and model preferences.</p>
      </div>
      <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border px-6 py-16 text-center">
        <div className="flex size-10 items-center justify-center rounded-lg bg-muted">
          <Settings className="size-5 text-muted-foreground" aria-hidden="true" />
        </div>
        <p className="text-sm font-medium">Settings are not available in the demo</p>
        <p className="max-w-sm text-sm text-muted-foreground">
          This page will host account, GitHub, and indexing configuration once the RepoLens backend is connected.
        </p>
      </div>
    </div>
  )
}

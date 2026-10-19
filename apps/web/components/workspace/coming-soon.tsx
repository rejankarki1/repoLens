import { Hourglass } from 'lucide-react'

/** Placeholder for workspace features that are not connected to the backend yet. */
export function ComingSoon({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-20 text-center">
      <div className="flex size-10 items-center justify-center rounded-lg bg-muted">
        <Hourglass className="size-5 text-muted-foreground" aria-hidden="true" />
      </div>
      <p className="text-sm font-medium">{title}</p>
      <p className="max-w-md text-sm text-muted-foreground">{children}</p>
    </div>
  )
}

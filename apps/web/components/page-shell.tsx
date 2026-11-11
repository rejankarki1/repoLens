import { cn } from '@/lib/utils'

/** One width, spacing and heading rhythm for the landing page, dashboard and repository list. */
export const PAGE_WIDTH = 'mx-auto w-full max-w-5xl px-4 sm:px-8'

export function PageShell({ className, children }: { className?: string; children: React.ReactNode }) {
  return <div className={cn(PAGE_WIDTH, 'flex flex-col gap-8 py-8 md:py-10', className)}>{children}</div>
}

export function PageHeader({
  title,
  description,
  actions,
  id,
}: {
  title: string
  description?: string
  actions?: React.ReactNode
  id?: string
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div className="flex min-w-0 flex-col gap-1">
        <h1 id={id} className="text-2xl font-semibold tracking-tight">
          {title}
        </h1>
        {description && <p className="max-w-2xl text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions}
    </div>
  )
}

export function SectionHeading({ id, children, action }: { id?: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <h2 id={id} className="text-sm font-medium">
        {children}
      </h2>
      {action}
    </div>
  )
}

'use client'

import Link from 'next/link'
import { ArrowRight } from 'lucide-react'
import { useShowcaseRepositories } from '@/lib/api/hooks'

/** A link into the live demo copy of a showcase repository; renders nothing if the server does not publish it. */
export function DemoLink({ repo, suffix, children }: { repo: string; suffix: string; children: React.ReactNode }) {
  const { data } = useShowcaseRepositories()
  const match = data?.find((r) => r.fullName.toLowerCase() === repo.toLowerCase())
  if (!match) return null
  return (
    <Link
      href={`/repos/${match.id}${suffix}`}
      className="inline-flex items-center gap-1.5 rounded text-sm font-medium text-primary transition-colors hover:underline focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/40"
    >
      {children}
      <ArrowRight className="size-3.5" aria-hidden="true" />
    </Link>
  )
}

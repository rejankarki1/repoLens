import { cn } from '@/lib/utils'

const LANGUAGE_COLORS: Record<string, string> = {
  TypeScript: 'bg-sky-500',
  JavaScript: 'bg-yellow-400',
  Python: 'bg-blue-500',
  Rust: 'bg-orange-600',
  Java: 'bg-amber-700',
  HTML: 'bg-orange-500',
  CSS: 'bg-violet-500',
  EJS: 'bg-lime-500',
  Shell: 'bg-emerald-500',
}

export function languageColor(language: string) {
  return LANGUAGE_COLORS[language] ?? 'bg-muted-foreground'
}

export function LanguageDot({ language, className }: { language: string; className?: string }) {
  return <span className={cn('inline-block size-2.5 shrink-0 rounded-full', languageColor(language), className)} aria-hidden="true" />
}

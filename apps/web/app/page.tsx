import Image from 'next/image'
import Link from 'next/link'
import { FileSearch, ListChecks, MessageSquareText } from 'lucide-react'
import { GitHubIcon, RepoLensLogo } from '@/components/icons'
import { DemoLink } from '@/components/landing/demo-link'
import { HeaderAction, HeroActions, HeroNote } from '@/components/landing/landing-actions'
import { DemoRepositories } from '@/components/landing/demo-repositories'
import { PAGE_WIDTH } from '@/components/page-shell'

const SOURCE_URL = 'https://github.com/rejankarki1/repoLens'

const STEPS = [
  { icon: ListChecks, title: 'Add a repository', text: 'Paste a public GitHub URL.' },
  { icon: FileSearch, title: 'It gets indexed', text: 'The code is split into searchable pieces and its imports are mapped.' },
  { icon: MessageSquareText, title: 'Ask and check', text: 'Every answer cites the files and lines it came from.' },
]

const FEATURES = [
  {
    image: '/landing/architecture.png',
    alt: 'The Architecture Explorer showing the Wanderlust repository as a diagram: boxes for the frontend pages and components and the backend controllers and API, joined by solid arrows for verified imports and dashed arrows for inferred relationships, with a minimap in the corner.',
    title: 'See how the pieces connect',
    text: 'An AI-identified diagram of a repository’s components. Pan and zoom, or select a component to see the real files behind it.',
    repo: 'rejankarki1/wanderlust',
    suffix: '/graph',
    link: 'Open this diagram',
  },
  {
    image: '/landing/code-explorer.png',
    alt: 'The Code Explorer showing a file tree and the Wanderlust listings controller with the geocoding function highlighted, and a panel of what the file imports and what imports it.',
    title: 'Browse the real source',
    text: 'A file tree, highlighted code, and a panel showing what each file imports and what imports it. Citations from Ask AI open right here.',
    repo: 'rejankarki1/wanderlust',
    suffix: '?tab=code&file=backend%2Fcontrollers%2Fapi%2Flistings.js&lines=84-96',
    link: 'Open this file',
  },
]

export default function Home() {
  return (
    <div className="min-h-dvh bg-background">
      <header className="border-b border-border">
        <div className={`${PAGE_WIDTH} flex h-14 items-center justify-between gap-4`}>
          <Link href="/" className="flex items-center gap-2.5 rounded focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/40">
            <RepoLensLogo className="size-6" />
            <span className="text-sm font-semibold tracking-tight">RepoLens</span>
          </Link>
          <nav aria-label="Site" className="flex items-center gap-1 sm:gap-3">
            <a href="#how-it-works" className="hidden rounded px-2 py-1 text-sm text-muted-foreground transition-colors hover:text-foreground sm:block">
              How it works
            </a>
            <a
              href={SOURCE_URL}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1.5 rounded px-2 py-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
            >
              <GitHubIcon className="size-4" />
              <span className="max-sm:sr-only">Source</span>
            </a>
            <HeaderAction />
          </nav>
        </div>
      </header>

      <main className={`${PAGE_WIDTH} flex flex-col gap-20 py-14 md:py-20`}>
        <section aria-labelledby="hero-heading" className="flex flex-col items-center gap-8 text-center">
          <div className="flex max-w-3xl flex-col items-center gap-4">
            <h1 id="hero-heading" className="text-balance text-4xl font-semibold tracking-tight md:text-5xl">
              Understand any codebase.
            </h1>
            <p className="max-w-2xl text-pretty text-lg leading-relaxed text-muted-foreground">
              Explore a public GitHub repository, see how its modules connect, and ask questions answered from the code itself, with citations to
              the exact files and lines.
            </p>
          </div>
          <div className="flex flex-wrap items-center justify-center gap-3">
            <HeroActions />
          </div>
          <HeroNote />

          <figure className="w-full max-w-4xl">
            <div className="overflow-hidden rounded-xl border border-border bg-card shadow-lg">
              <Image
                src="/landing/ask-ai-wanderlust.png"
                alt="The RepoLens Ask AI screen for the Wanderlust repository: the question “Where is the entry point?”, an answer with numbered source tags, and the cited lines of backend/server.js beside it."
                width={1200}
                height={900}
                priority
                className="h-auto w-full"
              />
            </div>
            <figcaption className="mt-3 text-xs text-muted-foreground">
              Ask AI answering a question about Wanderlust, a real repository. Each source tag opens the cited lines.
            </figcaption>
          </figure>
        </section>

        <section aria-labelledby="features-heading" className="flex flex-col gap-6">
          <div className="flex flex-col gap-1.5">
            <h2 id="features-heading" className="text-2xl font-semibold tracking-tight">
              Check the answer in the code itself
            </h2>
            <p className="max-w-2xl text-muted-foreground">Ask AI shows its sources. Two more views let you follow them.</p>
          </div>
          <div className="grid gap-6 md:grid-cols-2">
            {FEATURES.map(({ image, alt, title, text, repo, suffix, link }) => (
              <figure key={title} className="flex flex-col gap-4">
                <div className="overflow-hidden rounded-xl border border-border bg-card shadow-md">
                  <Image src={image} alt={alt} width={1200} height={900} className="h-auto w-full" />
                </div>
                <figcaption className="flex flex-col gap-1.5">
                  <h3 className="font-medium">{title}</h3>
                  <p className="text-sm leading-relaxed text-muted-foreground">{text}</p>
                  <DemoLink repo={repo} suffix={suffix}>
                    {link}
                  </DemoLink>
                </figcaption>
              </figure>
            ))}
          </div>
        </section>

        <DemoRepositories />

        <section id="how-it-works" aria-labelledby="how-heading" className="flex scroll-mt-8 flex-col gap-6">
          <div className="flex flex-col gap-1.5">
            <h2 id="how-heading" className="text-2xl font-semibold tracking-tight">
              How RepoLens works
            </h2>
            <p className="max-w-2xl text-muted-foreground">From a URL to answers you can check.</p>
          </div>
          <ol className="grid gap-4 md:grid-cols-3">
            {STEPS.map(({ icon: Icon, title, text }, i) => (
              <li key={title} className="flex flex-col gap-3 rounded-lg border border-border bg-card p-5">
                <div className="flex items-center gap-3">
                  <span className="flex size-8 items-center justify-center rounded-md bg-primary/10 text-primary">
                    <Icon className="size-4" aria-hidden="true" />
                  </span>
                  <span className="font-mono text-xs text-muted-foreground">Step {i + 1}</span>
                </div>
                <h3 className="font-medium">{title}</h3>
                <p className="text-sm leading-relaxed text-muted-foreground">{text}</p>
              </li>
            ))}
          </ol>
        </section>
      </main>

      <footer className="border-t border-border">
        <div className={`${PAGE_WIDTH} flex flex-wrap items-center justify-between gap-3 py-6 text-sm text-muted-foreground`}>
          <p>RepoLens reads public GitHub repositories. It never changes them.</p>
          <a href={SOURCE_URL} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 rounded transition-colors hover:text-foreground">
            <GitHubIcon className="size-4" />
            View the source on GitHub
          </a>
        </div>
      </footer>
    </div>
  )
}

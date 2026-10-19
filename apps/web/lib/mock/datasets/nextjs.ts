import type { RepositoryDataset } from '@/lib/types'
import { code, createCiter, file } from '@/lib/mock/helpers'

const files = [
  file({
    path: 'packages/next/src/bin/next.ts',
    group: 'entry',
    imports: ['packages/next/src/cli/next-dev.ts', 'packages/next/src/cli/next-build.ts'],
    exports: ['program'],
    summary: 'CLI entry point. Registers the dev, build and start commands with commander.',
    content: code(`
      #!/usr/bin/env node

      import '../server/require-hook'

      import { Command } from 'next/dist/compiled/commander'
      import { nextDev } from '../cli/next-dev'
      import { nextBuild } from '../cli/next-build'

      const program = new Command()

      program
        .name('next')
        .description('The Next.js CLI allows you to develop, build, start your application, and more.')
        .version(process.env.__NEXT_VERSION ?? '0.0.0', '-v, --version')

      program
        .command('dev', { isDefault: true })
        .description('Starts Next.js in development mode with hot-code reloading.')
        .argument('[directory]', 'A directory on which to build the application.')
        .option('-p, --port <port>', 'Specify a port number on which to start the application.')
        .option('--turbopack', 'Starts development mode using Turbopack.')
        .action((directory, options) => nextDev(options, directory))

      program
        .command('build')
        .description('Creates an optimized production build of your application.')
        .argument('[directory]', 'A directory on which to build the application.')
        .option('--profile', 'Enables production profiling for React.')
        .action((directory, options) => nextBuild(options, directory))

      program.parse(process.argv)
    `),
  }),
  file({
    path: 'packages/next/src/cli/next-dev.ts',
    group: 'entry',
    imports: ['packages/next/src/server/next.ts', 'packages/next/src/server/config.ts'],
    exports: ['nextDev'],
    summary: 'Implements `next dev`: loads config, picks a port, and boots the development server.',
    content: code(`
      import type { NextDevOptions } from './types'
      import { loadConfig } from '../server/config'
      import { createServer } from '../server/next'
      import { PHASE_DEVELOPMENT_SERVER } from '../shared/lib/constants'

      export async function nextDev(options: NextDevOptions, directory?: string) {
        const dir = directory ?? process.cwd()
        const port = Number(options.port ?? process.env.PORT ?? 3000)

        const config = await loadConfig(PHASE_DEVELOPMENT_SERVER, dir)

        const app = createServer({
          dir,
          dev: true,
          conf: config,
          port,
          turbo: Boolean(options.turbopack),
        })

        await app.prepare()

        console.log(\`  \u25b2 Next.js \${process.env.__NEXT_VERSION}\`)
        console.log(\`  - Local: http://localhost:\${port}\`)
      }
    `),
  }),
  file({
    path: 'packages/next/src/cli/next-build.ts',
    group: 'entry',
    imports: ['packages/next/src/server/config.ts'],
    exports: ['nextBuild'],
    summary: 'Implements `next build` and reports failures with a non-zero exit code.',
    content: code(`
      import { loadConfig } from '../server/config'
      import { PHASE_PRODUCTION_BUILD } from '../shared/lib/constants'
      import build from '../build'

      export async function nextBuild(options: { profile?: boolean }, directory?: string) {
        const dir = directory ?? process.cwd()

        try {
          await loadConfig(PHASE_PRODUCTION_BUILD, dir)
          await build(dir, Boolean(options.profile))
        } catch (err) {
          const isError = err instanceof Error
          console.error(isError ? err.message : err)
          process.exit(1)
        }
      }
    `),
  }),
  file({
    path: 'packages/next/src/server/next.ts',
    group: 'core',
    imports: ['packages/next/src/server/next-server.ts', 'packages/next/src/server/config.ts'],
    exports: ['createServer', 'NextServer'],
    summary: 'Public programmatic server API. Lazily instantiates the underlying NextNodeServer.',
    content: code(`
      import type { NextConfigComplete } from './config-shared'
      import NextNodeServer from './next-server'

      export interface NextServerOptions {
        dir?: string
        dev?: boolean
        port?: number
        turbo?: boolean
        conf: NextConfigComplete
      }

      export class NextServer {
        private serverPromise?: Promise<NextNodeServer>
        constructor(public options: NextServerOptions) {}

        getRequestHandler() {
          return async (req: IncomingMessage, res: ServerResponse) => {
            const server = await this.getServer()
            return server.handleRequest(req, res)
          }
        }

        async prepare() {
          const server = await this.getServer()
          await server.prepare()
        }

        private getServer() {
          if (!this.serverPromise) {
            this.serverPromise = Promise.resolve(new NextNodeServer(this.options))
          }
          return this.serverPromise
        }
      }

      export function createServer(options: NextServerOptions) {
        return new NextServer(options)
      }
    `),
  }),
  file({
    path: 'packages/next/src/server/next-server.ts',
    group: 'core',
    imports: ['packages/next/src/server/base-server.ts', 'packages/next/src/server/lib/router-utils.ts'],
    exports: ['NextNodeServer'],
    summary: 'Node.js server implementation: resolves routes, runs middleware, and renders App Router pages.',
    content: code(`
      import BaseServer from './base-server'
      import { resolveRoutes } from './lib/router-utils'

      export default class NextNodeServer extends BaseServer {
        async handleRequest(req: IncomingMessage, res: ServerResponse) {
          try {
            const match = await resolveRoutes(this.routes, req.url ?? '/')

            if (!match) {
              return this.render404(req, res)
            }

            if (match.type === 'route-handler') {
              return this.runRouteHandler(match, req, res)
            }

            return this.renderAppPage(match, req, res)
          } catch (err) {
            return this.renderError(err as Error, req, res)
          }
        }

        protected async renderError(err: Error, req: IncomingMessage, res: ServerResponse) {
          this.logError(err)
          res.statusCode = 500
          return this.renderErrorToHTML(err, req, res, '/_error')
        }
      }
    `),
  }),
  file({
    path: 'packages/next/src/server/base-server.ts',
    group: 'core',
    imports: ['packages/next/src/server/config.ts'],
    exports: ['BaseServer'],
    summary: 'Runtime-agnostic server base class shared by the Node.js and Edge servers.',
    content: code(`
      import type { NextConfigComplete } from './config-shared'

      export default abstract class BaseServer {
        protected nextConfig: NextConfigComplete
        protected routes: RouteDefinition[] = []

        constructor(options: { conf: NextConfigComplete; dir?: string }) {
          this.nextConfig = options.conf
        }

        async prepare(): Promise<void> {
          this.routes = await this.loadRouteManifest()
        }

        protected logError(err: Error) {
          console.error(err)
        }

        protected abstract loadRouteManifest(): Promise<RouteDefinition[]>
      }
    `),
  }),
  file({
    path: 'packages/next/src/server/config.ts',
    group: 'data',
    imports: [],
    exports: ['loadConfig'],
    summary: 'Loads and validates next.config.js, merging user values over the default configuration.',
    content: code(`
      import { defaultConfig, type NextConfigComplete } from './config-shared'
      import { findUp } from 'next/dist/compiled/find-up'

      const CONFIG_FILES = ['next.config.js', 'next.config.mjs', 'next.config.ts']

      export async function loadConfig(phase: string, dir: string): Promise<NextConfigComplete> {
        const path = await findUp(CONFIG_FILES, { cwd: dir })

        if (!path) {
          return { ...defaultConfig, configFile: undefined }
        }

        const userConfigModule = await import(path)
        const userConfig = typeof userConfigModule.default === 'function'
          ? await userConfigModule.default(phase, { defaultConfig })
          : userConfigModule.default

        return { ...defaultConfig, ...userConfig, configFile: path }
      }
    `),
  }),
  file({
    path: 'packages/next/src/server/lib/router-utils.ts',
    group: 'routing',
    imports: [],
    exports: ['resolveRoutes'],
    summary: 'Matches a request pathname against the compiled route manifest, including dynamic segments.',
    content: code(`
      export interface RouteMatch {
        type: 'app-page' | 'route-handler'
        page: string
        params: Record<string, string | string[]>
      }

      export async function resolveRoutes(routes: RouteDefinition[], url: string): Promise<RouteMatch | null> {
        const { pathname } = new URL(url, 'http://n')

        for (const route of routes) {
          const result = route.regex.exec(pathname)
          if (!result) continue

          const params: Record<string, string> = {}
          route.keys.forEach((key, i) => {
            params[key] = decodeURIComponent(result[i + 1])
          })

          return { type: route.type, page: route.page, params }
        }

        return null
      }
    `),
  }),
  file({
    path: 'packages/next/package.json',
    content: code(`
      {
        "name": "next",
        "version": "16.0.0",
        "description": "The React Framework",
        "main": "./dist/server/next.js",
        "license": "MIT",
        "bin": {
          "next": "./dist/bin/next"
        },
        "peerDependencies": {
          "react": "^19.0.0",
          "react-dom": "^19.0.0"
        }
      }
    `),
  }),
  file({
    path: 'readme.md',
    content: code(`
      # Next.js

      Used by some of the world's largest companies, Next.js enables you to create
      full-stack web applications by extending the latest React features.

      ## Getting started

      Visit https://nextjs.org/learn to get started with Next.js.

      ## Documentation

      Visit https://nextjs.org/docs to view the full documentation.
    `),
  }),
]

const cite = createCiter(files)

export const nextjsDataset: RepositoryDataset = {
  files,
  insights: {
    summary:
      'Next.js is a monorepo whose primary package, packages/next, contains the CLI, the build pipeline and the server runtime. The CLI in src/bin/next.ts dispatches to command modules that load next.config via loadConfig() and then either boot a NextServer or run a production build. At runtime NextNodeServer extends a runtime-agnostic BaseServer, resolves incoming URLs against a compiled route manifest, and renders App Router pages or route handlers. This sample index covers a representative subset of the package.',
    entryPoints: [
      { path: 'packages/next/src/bin/next.ts', name: 'next CLI', description: 'Registers dev, build and start commands.' },
      { path: 'packages/next/src/server/next.ts', name: 'createServer()', description: 'Programmatic custom-server API.' },
    ],
    keyModules: [
      { path: 'packages/next/src/server/next-server.ts', name: 'NextNodeServer', description: 'Node.js request handling and rendering.' },
      { path: 'packages/next/src/server/config.ts', name: 'loadConfig()', description: 'Resolves and merges next.config.' },
      { path: 'packages/next/src/server/lib/router-utils.ts', name: 'resolveRoutes()', description: 'Matches URLs to the route manifest.' },
    ],
  },
  answers: [
    {
      keywords: ['entry', 'api', 'start', 'cli', 'main'],
      content: `There are two entry points depending on how Next.js is started:

1. **CLI** \u2014 \`packages/next/src/bin/next.ts\` registers commands with \`commander\`. \`dev\` is the default command and delegates to \`nextDev()\`.
2. **Programmatic** \u2014 \`createServer()\` in \`server/next.ts\` returns a \`NextServer\` for custom servers.

~~~ts
const app = createServer({ dir, dev: true, conf: config, port })
await app.prepare()
~~~

Both paths converge on \`NextNodeServer#handleRequest\`.`,
      citations: [
        cite('packages/next/src/bin/next.ts', ".command('dev'", 7),
        cite('packages/next/src/cli/next-dev.ts', 'export async function nextDev', 18),
        cite('packages/next/src/server/next.ts', 'export function createServer', 3),
      ],
    },
    {
      keywords: ['error', 'exception', 'fail', '500'],
      content: `Request-time errors are caught inside \`NextNodeServer#handleRequest\`. Any exception thrown while resolving or rendering is forwarded to \`renderError()\`, which logs it, sets status **500**, and renders the \`/_error\` page.

~~~ts
} catch (err) {
  return this.renderError(err as Error, req, res)
}
~~~

Build-time errors are handled in \`nextBuild()\`, which prints the message and exits with code \`1\` so CI fails.`,
      citations: [
        cite('packages/next/src/server/next-server.ts', 'async handleRequest', 16),
        cite('packages/next/src/server/next-server.ts', 'protected async renderError', 5),
        cite('packages/next/src/cli/next-build.ts', 'try {', 8),
      ],
    },
  ],
  fallbackAnswer: {
    content: `This sample index only covers a subset of \`packages/next\`, so I can't answer that precisely.

The indexed files describe the **CLI** (\`src/bin\`, \`src/cli\`), the **server runtime** (\`src/server\`), and **configuration loading**. Try asking about the API entry point or error handling.`,
    citations: [cite('packages/next/src/server/next-server.ts', 'export default class NextNodeServer', 6)],
  },
}

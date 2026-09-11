// Legal content the web app carries, all of it produced at build time:
//
//   legal/THIRD_PARTY_NOTICES.txt  attributions for every npm package whose code
//                                  is in the bundle
//   legal/NOTICE.txt               the repository's NOTICE and LICENSE, so the
//   legal/LICENSE.txt              frontend image carries them too
//   virtual:mascope-legal          NOTICE's copyright line, for the About dialog
//                                  and the sign-in footer
//   <meta name="mascope-version">  the version this bundle was built as
//
// The attributions exist because the frontend image redistributes the code of
// every package in its bundle, and the notice clauses of MIT and BSD, and
// Apache-2.0 section 4(d), make the copyright and licence text a condition of
// doing so. They come from the bundler's module graph rather than from
// package.json, so they name what was actually bundled - no devDependencies, no
// build toolchain - and cannot rot the way a hand-kept list would. The server's
// Python packages are attributed by the backend image instead
// (tooling/third-party-notices.py): each image carries the notices for what it
// ships.
//
// The version tag lets the update banner name the build it is offering (it
// reads it from a freshly fetched index.html, see src/lib/update) and lets the
// About dialog compare the web app's build with the server's.
//
// The app is air-gapped by design - nothing is fetched from a third party at
// view time - so all of this is baked in rather than looked up. `vite dev`
// serves the same /legal/ paths, with a placeholder for the attributions, which
// need a production module graph.
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

const VIRTUAL_ID = 'virtual:mascope-legal'
const RESOLVED_VIRTUAL_ID = '\0' + VIRTUAL_ID

const MODULES = '/node_modules/'

// The files a package ships to satisfy its licence, by the names projects use.
const LICENCE_FILE = /^(licen[cs]e|copying|notice|copyright)([._-].*)?$/i

const RULE = '-'.repeat(79)
export const NO_FILES = '(The package ships no licence file; its declared licence is given above.)'

const HEADER = `Mascope web app - third-party notices

The Mascope web app includes the open-source npm packages below. Each entry
gives the package, its version and declared licence, followed by the licence
and notice files the package itself ships. Generated at build time from the
modules in the bundle (server/frontend/scripts/vite-plugin-legal.js) - do not
edit.
`

const DEV_NOTICES = `Third-party notices are generated from the production bundle.
Run \`npm run build\` and read dist/legal/THIRD_PARTY_NOTICES.txt.
`

/** The directory of the npm package a module id belongs to; null for our own code. */
export const packageDirOf = (id) => {
  const path = id.replace(/^\0/, '').split('?')[0].replaceAll('\\', '/')
  const at = path.lastIndexOf(MODULES)
  if (at < 0) return null
  const segments = path.slice(at + MODULES.length).split('/')
  const depth = segments[0].startsWith('@') ? 2 : 1
  if (segments.length <= depth) return null
  return path.slice(0, at + MODULES.length) + segments.slice(0, depth).join('/')
}

const declaredLicence = (manifest) => {
  if (typeof manifest.license === 'string') return manifest.license
  if (manifest.license?.type) return manifest.license.type
  // The deprecated `licenses` array, whose entries are alternatives.
  if (Array.isArray(manifest.licenses)) {
    return manifest.licenses.map((licence) => licence?.type ?? licence).join(' OR ')
  }
  return 'not declared'
}

const homepageOf = (manifest) => {
  const repository =
    typeof manifest.repository === 'string' ? manifest.repository : manifest.repository?.url
  return manifest.homepage ?? repository?.replace(/^git\+/, '') ?? null
}

const readPackage = (dir) => {
  const manifestPath = join(dir, 'package.json')
  const manifest = existsSync(manifestPath) ? JSON.parse(readFileSync(manifestPath, 'utf8')) : {}
  const files = readdirSync(dir, { withFileTypes: true })
    .filter((entry) => entry.isFile() && LICENCE_FILE.test(entry.name))
    .map((entry) => entry.name)
    .sort()
    // One line-ending convention, whatever each project committed, so the file
    // reads the same everywhere and a rebuild reproduces it byte for byte.
    .map((name) => ({ name, text: readFileSync(join(dir, name), 'utf8').replace(/\r\n?/g, '\n') }))
  return {
    name: manifest.name ?? dir.slice(dir.lastIndexOf(MODULES) + MODULES.length),
    version: manifest.version ?? '?',
    licence: declaredLicence(manifest),
    homepage: homepageOf(manifest),
    files
  }
}

// Plain code-unit order, not localeCompare: the same bundle must produce the
// same file on every machine.
const compare = (a, b) => (a < b ? -1 : a > b ? 1 : 0)

/** The packages behind `moduleIds`, one entry per name@version, sorted. */
export const collectPackages = (moduleIds) => {
  const dirs = new Set()
  for (const id of moduleIds) {
    const dir = packageDirOf(id)
    if (dir) dirs.add(dir)
  }
  const packages = new Map()
  for (const dir of dirs) {
    const found = readPackage(dir)
    packages.set(`${found.name}@${found.version}`, found)
  }
  return [...packages.values()].sort(
    (a, b) => compare(a.name, b.name) || compare(a.version, b.version)
  )
}

/** The notices file for `packages`, laid out like the server's. */
export const renderNotices = (packages) => {
  const lines = [HEADER, `${packages.length} packages.`, '']
  for (const found of packages) {
    lines.push(RULE, `${found.name} ${found.version}`, `License: ${found.licence}`)
    if (found.homepage) lines.push(found.homepage)
    lines.push('')
    if (found.files.length === 0) lines.push(NO_FILES, '')
    for (const file of found.files) lines.push(`--- ${file.name}`, file.text.trimEnd(), '')
  }
  return lines.join('\n') + '\n'
}

/** NOTICE's "Copyright ..." line, or null. */
export const copyrightLine = (notice) =>
  notice
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => /^copyright\b/i.test(line)) ?? null

/** The version in a serialized MASCOPE_RUNTIME, or null. */
const bakedVersion = (serialized) => {
  try {
    const version = JSON.parse(serialized)?.version
    return typeof version === 'string' && version ? version : null
  } catch {
    return null
  }
}

/** @param {{ repoRoot: string }} options - where NOTICE and LICENSE live */
export default function legal({ repoRoot }) {
  let version = null

  const repoFile = (name) => {
    const path = join(repoRoot, name)
    if (!existsSync(path)) {
      throw new Error(
        `${path} not found: the web app ships the repository's ${name}, so ` +
          'build from a full checkout (the image build copies it in)'
      )
    }
    return readFileSync(path, 'utf8')
  }

  return {
    name: 'mascope-legal',

    configResolved(config) {
      version = bakedVersion(config.env?.MASCOPE_RUNTIME)
    },

    resolveId(id) {
      return id === VIRTUAL_ID ? RESOLVED_VIRTUAL_ID : null
    },

    load(id) {
      if (id !== RESOLVED_VIRTUAL_ID) return null
      return `export const copyright = ${JSON.stringify(copyrightLine(repoFile('NOTICE')))}\n`
    },

    transformIndexHtml() {
      if (!version) return []
      return [
        { tag: 'meta', attrs: { name: 'mascope-version', content: version }, injectTo: 'head' }
      ]
    },

    configureServer(server) {
      const files = new Map([
        ['NOTICE.txt', () => repoFile('NOTICE')],
        ['LICENSE.txt', () => repoFile('LICENSE')],
        ['THIRD_PARTY_NOTICES.txt', () => DEV_NOTICES]
      ])
      server.middlewares.use('/legal', (req, res, next) => {
        const body = files.get((req.url ?? '').replace(/^\/+/, '').split('?')[0])
        if (!body) return next()
        res.setHeader('Content-Type', 'text/plain; charset=utf-8')
        res.end(body())
      })
    },

    generateBundle(_options, bundle) {
      // Every module in the graph, plus what each chunk reports it rendered.
      const ids = new Set(this.getModuleIds?.() ?? [])
      for (const output of Object.values(bundle)) {
        if (output.type !== 'chunk') continue
        for (const id of output.moduleIds ?? Object.keys(output.modules ?? {})) ids.add(id)
      }
      const packages = collectPackages(ids)
      if (packages.length === 0) {
        // An empty file would read as "no third-party code", which is never true.
        this.error('no npm packages found in the bundle - refusing to ship empty notices')
      }
      const emit = (fileName, source) => this.emitFile({ type: 'asset', fileName, source })
      emit('legal/THIRD_PARTY_NOTICES.txt', renderNotices(packages))
      emit('legal/NOTICE.txt', repoFile('NOTICE'))
      emit('legal/LICENSE.txt', repoFile('LICENSE'))
    }
  }
}

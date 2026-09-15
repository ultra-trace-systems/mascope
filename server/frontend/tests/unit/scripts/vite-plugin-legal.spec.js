import { describe, it, expect, beforeAll, afterAll } from 'vitest'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'

import legal, {
  NO_FILES,
  collectPackages,
  copyrightLine,
  packageDirOf,
  renderNotices
} from '../../../scripts/vite-plugin-legal.js'

// The attributions the web app ships are worth what two things are worth:
// finding the package behind every bundled module - scoped names, nested
// installs, Windows paths, and virtual ids that belong to no package - and
// carrying the licence text each package ships, not just its SPDX name, since
// the text is what the notice clauses of MIT, BSD and Apache-2.0 ask for. A
// fake node_modules keeps both hermetic.

let root

const write = (path, text) => {
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, text)
}
const inModules = (...parts) => join(root, 'node_modules', ...parts)

const failingContext = (ids) => ({
  getModuleIds: () => ids[Symbol.iterator](),
  emitFile: () => {},
  error: (message) => {
    throw new Error(message)
  }
})

beforeAll(() => {
  root = mkdtempSync(join(tmpdir(), 'mascope-legal-'))
  write(join(root, 'NOTICE'), 'Mascope\nCopyright 2021-2099 Example Oy\n\nMore.\n')
  write(join(root, 'LICENSE'), 'Apache License\nVersion 2.0\n')

  write(
    inModules('alpha', 'package.json'),
    JSON.stringify({
      name: 'alpha',
      version: '1.0.0',
      license: 'MIT',
      homepage: 'https://alpha.example'
    })
  )
  write(inModules('alpha', 'LICENSE'), 'MIT License\n\nCopyright (c) Alpha authors\n')
  write(inModules('alpha', 'README.md'), 'not a licence')
  write(
    inModules('@scope', 'beta', 'package.json'),
    JSON.stringify({
      name: '@scope/beta',
      version: '2.0.0',
      license: 'Apache-2.0',
      repository: { type: 'git', url: 'git+https://github.com/scope/beta.git' }
    })
  )
  write(inModules('@scope', 'beta', 'LICENSE.md'), 'Apache text')
  write(inModules('@scope', 'beta', 'NOTICE'), 'Beta notice')
  write(
    inModules('gamma', 'package.json'),
    JSON.stringify({
      name: 'gamma',
      version: '3.0.0',
      licenses: [{ type: 'MIT' }, { type: 'Apache-2.0' }]
    })
  )
})

afterAll(() => rmSync(root, { recursive: true, force: true }))

describe('packageDirOf', () => {
  it('finds the package a module belongs to', () => {
    expect(packageDirOf('/app/node_modules/vue/dist/vue.runtime.esm-bundler.js')).toBe(
      '/app/node_modules/vue'
    )
  })

  it('keeps both segments of a scoped name', () => {
    expect(packageDirOf('/app/node_modules/@uppy/core/lib/index.js')).toBe(
      '/app/node_modules/@uppy/core'
    )
  })

  it('attributes a nested install to the innermost package', () => {
    expect(packageDirOf('/app/node_modules/a/node_modules/b/index.js')).toBe(
      '/app/node_modules/a/node_modules/b'
    )
  })

  it('reads Windows paths, virtual prefixes and query suffixes', () => {
    expect(packageDirOf('C:\\app\\node_modules\\axios\\index.js')).toBe('C:/app/node_modules/axios')
    expect(packageDirOf('\0/app/node_modules/primevue/button/index.mjs?commonjs-proxy')).toBe(
      '/app/node_modules/primevue'
    )
  })

  it("claims nothing for our own code or the bundler's helpers", () => {
    expect(packageDirOf('/app/src/main.js')).toBeNull()
    expect(packageDirOf('\0vite/preload-helper.js')).toBeNull()
  })
})

describe('collectPackages', () => {
  it('lists each bundled package once, sorted, with the licence files it ships', () => {
    const packages = collectPackages([
      inModules('gamma', 'index.js'),
      inModules('alpha', 'dist', 'a.js'),
      inModules('alpha', 'dist', 'b.js'),
      inModules('@scope', 'beta', 'lib', 'index.js'),
      join(root, 'src', 'main.js')
    ])

    expect(packages.map((found) => `${found.name}@${found.version}`)).toEqual([
      '@scope/beta@2.0.0',
      'alpha@1.0.0',
      'gamma@3.0.0'
    ])
    const [beta, alpha, gamma] = packages
    expect(alpha).toMatchObject({
      licence: 'MIT',
      homepage: 'https://alpha.example',
      files: [{ name: 'LICENSE', text: 'MIT License\n\nCopyright (c) Alpha authors\n' }]
    })
    expect(beta.files.map((file) => file.name)).toEqual(['LICENSE.md', 'NOTICE'])
    expect(beta.homepage).toBe('https://github.com/scope/beta.git')
    // The deprecated `licenses` array lists alternatives.
    expect(gamma).toMatchObject({ licence: 'MIT OR Apache-2.0', files: [] })
  })
})

describe('renderNotices', () => {
  it('carries every package, its declared licence and its licence text', () => {
    const text = renderNotices(
      collectPackages([inModules('alpha', 'x.js'), inModules('gamma', 'x.js')])
    )

    expect(text.startsWith('Mascope web app - third-party notices')).toBe(true)
    expect(text).toContain('2 packages.')
    expect(text).toContain(
      'alpha 1.0.0\nLicense: MIT\nhttps://alpha.example\n\n' +
        '--- LICENSE\nMIT License\n\nCopyright (c) Alpha authors\n'
    )
    expect(text).toContain(`gamma 3.0.0\nLicense: MIT OR Apache-2.0\n\n${NO_FILES}`)
  })
})

describe('copyrightLine', () => {
  it('finds the copyright line in a NOTICE', () => {
    expect(copyrightLine('Mascope\n  Copyright 2021-2026 Example Oy\n')).toBe(
      'Copyright 2021-2026 Example Oy'
    )
    expect(copyrightLine('no such line')).toBeNull()
  })
})

describe('the plugin', () => {
  const configured = (runtime) => {
    const plugin = legal({ repoRoot: root })
    plugin.configResolved({ env: runtime === undefined ? {} : { MASCOPE_RUNTIME: runtime } })
    return plugin
  }

  it('tags index.html with the version the bundle was built as', () => {
    const plugin = configured(JSON.stringify({ version: 'v1.8.0', meta: {} }))

    expect(plugin.transformIndexHtml()).toEqual([
      { tag: 'meta', attrs: { name: 'mascope-version', content: 'v1.8.0' }, injectTo: 'head' }
    ])
  })

  it('adds no tag when the build was given no version', () => {
    expect(configured(undefined).transformIndexHtml()).toEqual([])
    expect(configured('not json').transformIndexHtml()).toEqual([])
    expect(configured(JSON.stringify({ version: '' })).transformIndexHtml()).toEqual([])
  })

  it("exposes NOTICE's copyright line as a virtual module", () => {
    const plugin = configured()
    const id = plugin.resolveId('virtual:mascope-legal')

    expect(plugin.load(id)).toBe('export const copyright = "Copyright 2021-2099 Example Oy"\n')
    expect(plugin.resolveId('./elsewhere.js')).toBeNull()
  })

  it('emits the notices, NOTICE and LICENSE with the bundle', () => {
    const emitted = {}
    const context = {
      ...failingContext([inModules('alpha', 'x.js')]),
      emitFile: ({ fileName, source }) => {
        emitted[fileName] = source
      }
    }

    // A chunk's own module list counts too, for a bundler without getModuleIds.
    configured().generateBundle.call(
      context,
      {},
      { 'index.js': { type: 'chunk', moduleIds: [inModules('gamma', 'x.js')] } }
    )

    expect(Object.keys(emitted).sort()).toEqual([
      'legal/LICENSE.txt',
      'legal/NOTICE.txt',
      'legal/THIRD_PARTY_NOTICES.txt'
    ])
    expect(emitted['legal/THIRD_PARTY_NOTICES.txt']).toContain('alpha 1.0.0')
    expect(emitted['legal/THIRD_PARTY_NOTICES.txt']).toContain('gamma 3.0.0')
    expect(emitted['legal/NOTICE.txt']).toContain('Copyright 2021-2099 Example Oy')
    expect(emitted['legal/LICENSE.txt']).toContain('Apache License')
  })

  it('refuses to ship empty notices', () => {
    const context = failingContext([join(root, 'src', 'main.js')])

    expect(() => configured().generateBundle.call(context, {}, {})).toThrow(
      /refusing to ship empty notices/
    )
  })

  it('fails the build when NOTICE is missing, rather than shipping without it', () => {
    const plugin = legal({ repoRoot: join(root, 'nowhere') })
    plugin.configResolved({ env: {} })
    const context = failingContext([inModules('alpha', 'x.js')])

    expect(() => plugin.generateBundle.call(context, {}, {})).toThrow(/NOTICE not found/)
  })
})

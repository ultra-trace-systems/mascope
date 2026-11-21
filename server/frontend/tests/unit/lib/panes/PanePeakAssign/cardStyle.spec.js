import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

// The card keeps to the design system's type and colour
// (frontend_design_system.md, sections 1 and 3): three type sizes, the card's
// local constants until the app's type tokens land (MAS-160); at most four
// opacity steps; and no colour that is not a token. The review that planned the
// card counted 14 font sizes and 9 opacities in it, and a hard-coded purple.

const ROOT = join(import.meta.dirname, '..', '..', '..', '..', '..', 'src', 'lib', 'panes')
const FILES = [
  join(ROOT, 'PanePeakAssign', 'PanePeakAssign.vue'),
  ...readdirSync(join(ROOT, 'PanePeakAssign', 'card'))
    .filter((name) => name.endsWith('.vue'))
    .map((name) => join(ROOT, 'PanePeakAssign', 'card', name))
]
const styleOf = (path) => {
  const text = readFileSync(path, 'utf8')
  return text.slice(Math.max(0, text.indexOf('<style')))
}
const values = (property) =>
  FILES.flatMap((path) =>
    [...styleOf(path).matchAll(new RegExp(`(?<![\\w-])${property}:\\s*([^;]+);`, 'g'))].map(
      (match) => ({ file: path.split('/').at(-1), value: match[1].trim() })
    )
  )

describe('the card’s type and colour', () => {
  it('has a section of its own for every part of the card', () => {
    expect(FILES.length).toBeGreaterThanOrEqual(10)
  })

  it('sets its type in three sizes', () => {
    const sizes = new Set(values('font-size').map(({ value }) => value))
    // The batch's mark on a borrowed verdict is the design note's 9px glyph.
    sizes.delete('9px')
    expect([...sizes].sort()).toEqual([
      'inherit',
      'var(--card-text-base)',
      'var(--card-text-lg)',
      'var(--card-text-sm)'
    ])
  })

  it('declares the three sizes once, on the card', () => {
    const declared = values('--card-text-(?:lg|base|sm)')
    expect(declared.map(({ file }) => file)).toEqual([
      'PanePeakAssign.vue',
      'PanePeakAssign.vue',
      'PanePeakAssign.vue'
    ])
  })

  it('dims in at most four steps', () => {
    const steps = new Set(values('opacity').map(({ value }) => value))
    expect(steps.size).toBeLessThanOrEqual(4)
  })

  it('names every colour by its token', () => {
    const literal = /#[0-9a-f]{3,8}\b|\b(?:rgba?|hsla?)\(/i
    const found = FILES.filter((path) => literal.test(styleOf(path))).map((path) =>
      path.split('/').at(-1)
    )
    expect(found).toEqual([])
  })

  it('takes one column of tiles in a narrow card', () => {
    const tiles = styleOf(join(ROOT, 'PanePeakAssign', 'card', 'CardMeasurements.vue'))
    expect(tiles).toMatch(
      /@container card \(max-width: 340px\)\s*\{\s*\.tiles\s*\{\s*grid-template-columns: minmax\(0, 1fr\);/
    )
    expect(styleOf(FILES[0])).toMatch(/container: card \/ inline-size;/)
  })

  it('pins the verdict bar at the foot of the card', () => {
    const bar = styleOf(join(ROOT, 'PanePeakAssign', 'card', 'CardVerdictBar.vue'))
    expect(bar).toMatch(/\.verdict-bar \{[^}]*position: sticky;[^}]*bottom: 0;/)
  })
})

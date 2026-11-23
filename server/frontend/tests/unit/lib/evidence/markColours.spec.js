import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import palette from '@/palette.json'
import { TONES } from '@/lib/evidence/marks'

// The colours the marks draw in, measured on both grounds. The values are read
// off style.css itself rather than off a rendered page: happy-dom does not
// resolve custom properties declared inside the stylesheet's cascade layers,
// and reading the file measures exactly what ships.
//
// Text takes WCAG AA's 4.5:1 (1.4.3). A badge's glyph is a graphic, which
// takes 3:1 (1.4.11); its label is its accessible name and its tooltip.

const STYLE = readFileSync(
  join(import.meta.dirname, '..', '..', '..', '..', 'src', 'style.css'),
  'utf8'
)

/** `text` as a regular expression that matches it literally. */
const literal = (text) => text.replace(/[.*+?^${}()|[\]\\/>]/g, '\\$&')

/** The custom properties every `<selector> {` block of style.css declares, merged. */
function tokens(selector) {
  const found = {}
  const opener = new RegExp(`(^|\\n)\\s*${literal(selector)}\\s*\\{([^}]*)\\}`, 'g')
  for (const [, , body] of STYLE.matchAll(opener)) {
    for (const [, name, value] of body.matchAll(/(--[\w-]+):\s*([^;]+);/g))
      found[name] = value.trim()
  }
  return found
}
const LIGHT = tokens(':root')
const DARK = { ...LIGHT, ...tokens(':root.darkmode') }

// The theme's surface ramp (theme.js): surface.N is the palette's swatch at
// lightness 100 - N / 10, surface.0 white. Aura's content ground is surface.0
// on light and surface.900 on dark; text is surface.700 / surface.0 and muted
// text surface.500 / surface.400.
const swatch = (color, lightness) =>
  palette.find((entry) => entry.color === color && entry.lightness === lightness).hex
const surface = (ramp, n) => (n === 0 ? '#ffffff' : swatch(ramp, 100 - n / 10))
const GROUNDS = {
  light: [surface('offwhite', 0), surface('offwhite', 50)],
  dark: [surface('charcoal', 900), surface('charcoal', 950)]
}
const THEME = {
  light: {
    '--p-text-color': surface('offwhite', 700),
    '--p-text-muted-color': surface('offwhite', 500)
  },
  dark: {
    '--p-text-color': surface('charcoal', 0),
    '--p-text-muted-color': surface('charcoal', 400)
  }
}

const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))
const luminance = (hex) => {
  const [r, g, b] = rgb(hex).map((c) => {
    const s = c / 255
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
const contrast = (a, b) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}
/** `share` of `hex` over `ground`, as color-mix(in srgb) draws a chip's tint. */
const mix = (hex, ground, share) => {
  const [a, b] = [rgb(hex), rgb(ground)]
  return `#${a
    .map((c, i) =>
      Math.round(c * share + b[i] * (1 - share))
        .toString(16)
        .padStart(2, '0')
    )
    .join('')}`
}

const resolve = (value, theme) => {
  const name = value.match(/^var\((--[\w-]+)\)$/)?.[1]
  if (!name) return value
  const scheme = theme === 'light' ? LIGHT : DARK
  return scheme[name] ?? THEME[theme][name]
}

describe('the marks’ colours on both grounds', () => {
  it('declares the role token for each theme', () => {
    expect(LIGHT['--role-reagent']).toMatch(/^#[0-9a-f]{6}$/i)
    expect(DARK['--role-reagent']).toMatch(/^#[0-9a-f]{6}$/i)
    expect(DARK['--role-reagent']).not.toBe(LIGHT['--role-reagent'])
  })

  // The role chip draws its label in the token on a 10 % tint of it.
  it('clears 4.5:1 for the role chip’s text, on the ground and on its own tint', () => {
    for (const theme of ['light', 'dark']) {
      const role = theme === 'light' ? LIGHT['--role-reagent'] : DARK['--role-reagent']
      for (const ground of GROUNDS[theme]) {
        expect(contrast(role, ground), `${theme} on ${ground}`).toBeGreaterThanOrEqual(4.5)
        expect(contrast(role, mix(role, ground, 0.1)), `${theme} tint`).toBeGreaterThanOrEqual(4.5)
      }
    }
  })

  // The guide's Series 3 purple the design-system note named: the swatch
  // that says it cannot carry the role as text on either ground.
  it('needed a pair: the guide’s purple alone misses AA on both grounds', () => {
    expect(contrast('#8266EA', GROUNDS.light[0])).toBeLessThan(4.5)
    expect(contrast('#8266EA', GROUNDS.dark[0])).toBeLessThan(4.5)
  })

  it('clears 3:1 for every badge tone’s glyph, on the ground and on the badge’s tint', () => {
    for (const theme of ['light', 'dark']) {
      for (const tone of Object.values(TONES)) {
        const color = resolve(tone.color, theme)
        expect(color, `${theme} ${tone.key}`).toMatch(/^#[0-9a-f]{6}$/i)
        for (const ground of GROUNDS[theme]) {
          const label = `${theme} ${tone.key} on ${ground}`
          expect(contrast(color, ground), label).toBeGreaterThanOrEqual(3)
          expect(contrast(color, mix(color, ground, 0.1)), label).toBeGreaterThanOrEqual(3)
        }
      }
    }
  })
})

// Where a reading is still drawn in words rather than as a badge - the ratio
// column of the Batch peaks ledger - it takes the tone the registry gives
// it; and a person's act, wherever it is marked, the text colour.
describe('the marks drawn outside the badge', () => {
  const sfc = (...path) =>
    readFileSync(join(import.meta.dirname, '..', '..', '..', '..', 'src', ...path), 'utf8')
  const colourOf = (source, selector) =>
    source.match(new RegExp(`\\n${literal(selector)} \\{[^}]*?color: ([^;]+);`))?.[1]

  // The batch ledger draws the ratio grade as the registry's badge, in Across
  // the batch, and colours no grade of its own.
  it('leaves the ratio grades to the registry in the batch ledger', () => {
    const ledger = sfc('lib', 'panes', 'PaneBrowserMatch', 'PaneBrowserBatchPeaks.vue')
    expect(colourOf(ledger, '.continuity.stable')).toBeUndefined()
    expect(colourOf(ledger, '.continuity.red')).toBeUndefined()
  })

  it('draws the card’s hand in the human tone, never the accent', () => {
    const card = sfc('lib', 'panes', 'PanePeakAssign', 'card', 'CardNotes.vue')
    expect(colourOf(card, '.manual-note > .manual-icon')).toBe(TONES.human.color)
  })
})

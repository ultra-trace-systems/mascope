import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join, relative } from 'node:path'

// One mark, one meaning (docs/dev/assignment_ledger_and_card.md, section 5).
// Five glyphs each carried three or four meanings across the two ledgers, the
// inspector card and the Base components, and a dashed border meant seven
// things in the card alone. This reads src/ for each of them and holds every
// one to the single meaning it keeps, so a new use of an old double meaning
// fails here rather than in a reader's head.

const SRC = join(import.meta.dirname, '..', '..', '..', '..', 'src')

function sources(dir) {
  const found = []
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) found.push(...sources(path))
    else if (/\.(vue|js|css)$/.test(entry.name)) found.push(path)
  }
  return found
}
const FILES = sources(SRC).map((path) => ({
  path: relative(SRC, path).split('\\').join('/'),
  text: readFileSync(path, 'utf8')
}))

/** How many times each file under src/ names `needle`, the files that do. */
function uses(needle) {
  const counts = {}
  for (const { path, text } of FILES) {
    const n = text.split(needle).length - 1
    if (n) counts[path] = n
  }
  return counts
}

describe('the retired double meanings', () => {
  // The verdict column's header, in each ledger. The tier wears a dot, an
  // unjudged row nothing, and the batch facts their own check.
  it('keeps the seal-check for the verdict column alone', () => {
    expect(uses('ph-seal-check')).toEqual({
      'lib/panes/PaneBrowserMatch/PaneBrowserAssignment.vue': 1,
      'lib/panes/PaneBrowserMatch/PaneBrowserBatchPeaks.vue': 1
    })
  })

  // History: the banner over a ledger an earlier run left, Assign batch's
  // Run history, and the record of deleted datasets. A grade or a verdict the
  // ledger has moved past wears the out-of-date arrows.
  it('keeps the clock for history alone', () => {
    expect(uses('ph-clock-counter-clockwise')).toEqual({
      'lib/panes/PaneBrowserMatch/BatchAssignControl.vue': 1,
      'lib/panes/PaneBrowserMatch/PaneBrowserBatchPeaks.vue': 1,
      'lib/dialogs/DialogDeletedDatasets.vue': 1,
      'lib/toolbars/ToolbarAppFilters/SidebarMenu/UserSettingsPane.vue': 1
    })
  })

  // Gone from the assignment views: the listed name is written out, and the
  // reagent role is its colour. What stays is a file's chemistry, which the
  // processing views ask for.
  it('keeps the flask out of the assignment views, for a file’s chemistry', () => {
    expect(uses('ph-flask')).toEqual({
      'lib/processingStatus.js': 1,
      'lib/panes/PaneTabAcquisitions.vue': 1
    })
  })

  // A person changed this: the registry's reading, the tier chip's mark, and
  // the card's two notes of a change by hand. "Use this" and the search's
  // assign buttons wear the pin, an empty state the cursor.
  it('keeps the hand for a change a person made', () => {
    expect(uses('ph-hand-pointing')).toEqual({
      'lib/evidence/marks.js': 1,
      'lib/base/BaseTierTag.vue': 1,
      'lib/panes/PanePeakAssign/card/CardNotes.vue': 2
    })
  })

  // Co-variation: the registry's reading. The step that reads it is a stage
  // of Assign batch now, with no button of its own; the artifact role is its
  // colour, and the chart's empty state the cursor.
  it('keeps the wave for co-variation', () => {
    expect(uses('ph-wave-sine')).toEqual({
      'lib/evidence/marks.js': 1
    })
  })

  // A dashed border means not yet known: the unassigned tier's ring, and
  // nothing else the ledgers, the card or the Base components draw.
  it('draws a dashed border for the unassigned tier alone', () => {
    const scope = FILES.filter(({ path }) =>
      /^lib\/(base|panes\/PaneBrowserMatch|panes\/PanePeakAssign)\//.test(path)
    )
    const dashed = {}
    for (const { path, text } of scope) {
      const style = text.slice(Math.max(0, text.indexOf('<style')))
      const declarations = style.match(/[\w-]+\s*:[^;{}]*\bdashed\b[^;{}]*;/g) ?? []
      if (declarations.length) dashed[path] = declarations
    }
    expect(dashed).toEqual({ 'lib/base/BaseTierTag.vue': ['border-style: dashed;'] })
  })
})

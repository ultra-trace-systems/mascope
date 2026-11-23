import { test, expect } from './fixtures'

/**
 * One ledger in two scopes (docs/dev/assignment_ledger_and_card.md, section
 * 3.1): the batch's Batch peaks ledger and a sample's Assignments ledger are
 * the same component, with the same header line, the same columns in sentence
 * case and multi-select tier chips.
 *
 * Reads the demo data and changes nothing. It needs a sample with a completed
 * assignment run, whose batch therefore holds batch peaks, and the assignment
 * views switched on, and skips, saying which, on a stack that has neither: the
 * demo bundle ships no runs, and the CI stack switches assignment on in the
 * backend only.
 */

/** The first demo sample with a completed assignment run. */
async function searchAssignedSample(api) {
  for (const workspace of (await api.get('/workspaces')).data) {
    const datasets = (await api.get(`/workspaces/${workspace.workspace_id}/datasets`)).data
    for (const dataset of datasets) {
      const batches = (await api.get(`/sample/batches?dataset_id=${dataset.dataset_id}`)).data
      for (const batch of batches) {
        const samples = (await api.get(`/samples?sample_batch_id=${batch.sample_batch_id}`)).data
        for (const sample of samples.slice(0, 5)) {
          const runs = await api.get(`/peak-assignments/sample/${sample.sample_item_id}/runs`)
          const list = Array.isArray(runs) ? runs : (runs?.data ?? [])
          if (list.some((run) => run.status === 'completed')) {
            return { workspace, dataset, batch, sample }
          }
        }
      }
    }
  }
  return null
}

let found
const findAssignedSample = (api) => (found ??= searchAssignedSample(api))

/** Open the app on the batch, or on one of its samples, with the ledger in view. */
async function openLedger(page, target, { sample = false } = {}) {
  const query = new URLSearchParams({
    w: target.workspace.workspace_id,
    d: target.dataset.dataset_id,
    b: target.batch.sample_batch_id,
    ...(sample ? { s: target.sample.sample_item_id } : {}),
    tab: sample ? 'sample' : 'batch',
    v: '1'
  })
  await page.goto(`/?${query}`)
  const modeSwitch = page.getByRole('group', { name: 'Targets or assignments' })
  const shown = await modeSwitch
    .waitFor({ state: 'visible', timeout: 30_000 })
    .then(() => true)
    .catch(() => false)
  test.skip(!shown, 'The assignment views are switched off on this stack')
  await modeSwitch.getByText('Assignments').click()
}

/**
 * The ledger's column headers, as the table shows them. The header line's
 * count can show while the rows still load, the table not yet drawn, so wait
 * for it first.
 */
async function headers(page) {
  const cells = page.locator('.ledger .p-datatable thead th')
  await cells.first().waitFor({ timeout: 30_000 })
  return (await cells.allInnerTexts()).map((text) => text.trim().split('\n')[0])
}

test.describe('the assignment ledger', () => {
  test.use({ viewport: { width: 1600, height: 900 } })

  test('opens the batch ledger and a sample ledger, one ledger in both scopes', async ({
    page,
    api
  }) => {
    const target = await findAssignedSample(api)
    test.skip(!target, 'No demo sample has a completed assignment run')

    // The batch scope: its count in the header line, its columns, its chips.
    await openLedger(page, target)
    const count = page.getByTestId('ledger-count')
    await expect(count).toHaveText(/^[\d,]+ batch peaks?$/, { timeout: 30_000 })
    const batchHeaders = await headers(page)
    for (const name of [
      'm/z',
      'Compound',
      'Ion',
      'Max intensity',
      'Tier',
      'Samples',
      'Across the batch'
    ]) {
      expect(batchHeaders).toContain(name)
    }
    expect(batchHeaders).not.toContain('Listed')

    // Multi-select in both scopes: two chips on at once.
    const chip = (name) => page.locator('.tier-stat').filter({ hasText: new RegExp(` ${name}$`) })
    await chip('assigned').click()
    await chip('candidate').click()
    await expect(chip('assigned')).toHaveAttribute('aria-pressed', 'true')
    await expect(chip('candidate')).toHaveAttribute('aria-pressed', 'true')

    // The sample scope: the same header line and columns, and its own.
    await openLedger(page, target, { sample: true })
    await expect(count).toHaveText(/^[\d,]+ peaks?$/, { timeout: 30_000 })
    const sampleHeaders = await headers(page)
    for (const name of ['m/z', 'Compound', 'Ion', 'Intensity', 'Tier']) {
      expect(sampleHeaders).toContain(name)
    }
    expect(sampleHeaders).not.toContain('Samples')
    await chip('assigned').click()
    await chip('candidate').click()
    await expect(chip('assigned')).toHaveAttribute('aria-pressed', 'true')
    await expect(chip('candidate')).toHaveAttribute('aria-pressed', 'true')
    // A ledger row with a formula, its verdict cell the button that judges it.
    await expect(
      page.getByRole('button', { name: /Record a verdict|Verification verdict/ }).first()
    ).toBeVisible()
  })
  // The columns and rows (docs/dev/assignment_ledger_and_card.md, sections 3.2
  // and 3.3): the batch ledger opens sorted by its max intensity, brightest
  // first, and a family's caret unfolds its isotopologue lines under it.
  test('opens the batch ledger sorted by intensity, and unfolds a family', async ({
    page,
    api
  }) => {
    const target = await findAssignedSample(api)
    test.skip(!target, 'No demo sample has a completed assignment run')

    await openLedger(page, target)
    await expect(page.getByTestId('ledger-count')).toHaveText(/^[\d,]+ batch peaks?$/, {
      timeout: 30_000
    })
    // Sorted by intensity, descending, the header says so.
    const intensity = page.locator('.ledger thead th').filter({ hasText: 'Max intensity' })
    await expect(intensity).toHaveAttribute('aria-sort', 'descending')
    // The compact numbers down the first rows, read back, never rise.
    const values = (await page.getByTestId('intensity-cell').allInnerTexts())
      .slice(0, 8)
      .map((text) => {
        const [, n, unit] = text.trim().match(/^(-?[\d.]+)([kMGT]?)$/) ?? []
        return Number(n) * 1000 ** ['', 'k', 'M', 'G', 'T'].indexOf(unit ?? '')
      })
    expect(values.length).toBeGreaterThan(1)
    for (let i = 1; i < values.length; i++) expect(values[i]).toBeLessThanOrEqual(values[i - 1])

    // A family unfolds from its caret: its lines show under its row.
    const caret = page.getByTestId('family-caret').first()
    const hasFamily = await caret
      .waitFor({ timeout: 10_000 })
      .then(() => true)
      .catch(() => false)
    test.skip(!hasFamily, 'No batch peak of the demo batch has an isotopologue line')
    const lines = Number((await caret.innerText()).trim())
    const subRows = page.locator('.ledger tbody tr.ledger-line')
    await expect(subRows).toHaveCount(0)
    await expect(caret).toHaveAttribute('aria-expanded', 'false')
    await caret.click()
    await expect(caret).toHaveAttribute('aria-expanded', 'true')
    await expect(subRows).toHaveCount(lines)
    // A sub-row carries its label, and no verdict of its own.
    await expect(subRows.first().getByTestId('line-cell')).toBeVisible()
    await expect(subRows.first().locator('.verdict-cell')).toHaveCount(0)
    await caret.click()
    await expect(subRows).toHaveCount(0)
  })

  // The filter bar's choices are the scope's and outlive the pane: opening a
  // sample swaps the batch ledger out, and coming back finds the search and
  // the chips where they were left, while the sample's ledger keeps its own.
  test('keeps a filter set in the batch scope after visiting a sample and coming back', async ({
    page,
    api
  }) => {
    const target = await findAssignedSample(api)
    test.skip(!target, 'No demo sample has a completed assignment run')

    await openLedger(page, target)
    const count = page.getByTestId('ledger-count')
    await expect(count).toHaveText(/^[\d,]+ batch peaks?$/, { timeout: 30_000 })
    const search = page.getByTestId('ledger-search')
    const chip = (name) => page.locator('.tier-stat').filter({ hasText: new RegExp(` ${name}$`) })
    const firstChip = page.locator('.tier-stat').first()
    const chipName = (await firstChip.innerText()).replace(/^[\d,]+\s+/, '')

    await firstChip.click()
    await search.fill('C')
    await expect(chip(chipName)).toHaveAttribute('aria-pressed', 'true')
    const rows = page.locator('.ledger .p-datatable tbody tr')
    await expect(rows.first()).toBeVisible()

    // Visit a sample from the ledger itself: its brightest one, in focus.
    await page
      .getByRole('button', { name: 'Open the brightest sample with this peak in focus' })
      .first()
      .click()
    await expect(count).toHaveText(/^[\d,]+ peaks?$/, { timeout: 30_000 })
    // The sample's ledger keeps its own choices.
    await expect(search).toHaveValue('')

    // And back to the batch, as a reader goes: removing the sample's filter
    // chip.
    await page.locator('.p-chip:has(.pi-tag) .p-chip-remove-icon').click()
    await expect(count).toHaveText(/^[\d,]+ batch peaks?$/, { timeout: 30_000 })
    await expect(search).toHaveValue('C')
    await expect(chip(chipName)).toHaveAttribute('aria-pressed', 'true')

    // And after a reload.
    await page.reload()
    await expect(count).toHaveText(/^[\d,]+ batch peaks?$/, { timeout: 30_000 })
    await expect(search).toHaveValue('C')
    await expect(chip(chipName)).toHaveAttribute('aria-pressed', 'true')
  })
})

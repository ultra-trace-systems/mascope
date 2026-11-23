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
    for (const name of ['m/z', 'Intensity', 'Formula', 'Listed', 'Tier', 'Samples']) {
      expect(batchHeaders).toContain(name)
    }
    expect(batchHeaders).not.toContain('Ionization')

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
    for (const name of ['m/z', 'Intensity', 'Formula', 'Listed', 'Ionization', 'Tier']) {
      expect(sampleHeaders).toContain(name)
    }
    await chip('assigned').click()
    await chip('candidate').click()
    await expect(chip('assigned')).toHaveAttribute('aria-pressed', 'true')
    await expect(chip('candidate')).toHaveAttribute('aria-pressed', 'true')
    // A ledger row with a formula, its verdict cell the button that judges it.
    await expect(
      page.getByRole('button', { name: /Record a verdict|Verification verdict/ }).first()
    ).toBeVisible()
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

import { test, expect } from './fixtures'

/**
 * The peak inspector answers before it explains
 * (docs/dev/assignment_ledger_and_card.md, section 4.1): at the column's
 * default width, on a 1280 px window, its first screen holds the answer, the
 * Why box and the verdict bar, whatever the folded sections below hold.
 *
 * Reads the demo data and changes nothing. It needs a sample with a completed
 * assignment run and the assignment views switched on, and skips, saying
 * which, on a stack that has neither: the demo bundle ships no runs, and the
 * CI stack switches assignment on in the backend only.
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

test.describe('the peak inspector card', () => {
  test.use({ viewport: { width: 1280, height: 800 } })

  test('shows the answer, the Why box and the verdict bar on its first screen', async ({
    page,
    api
  }) => {
    const target = await findAssignedSample(api)
    test.skip(!target, 'No demo sample has a completed assignment run')

    const query = new URLSearchParams({
      w: target.workspace.workspace_id,
      d: target.dataset.dataset_id,
      b: target.batch.sample_batch_id,
      s: target.sample.sample_item_id,
      tab: 'sample',
      v: '1'
    })
    await page.goto(`/?${query}`)
    const ledgerTab = page.getByText('Assignments', { exact: true }).first()
    const shown = await ledgerTab
      .waitFor({ timeout: 30_000 })
      .then(() => true)
      .catch(() => false)
    test.skip(!shown, 'The assignment views are switched off on this stack')
    await ledgerTab.click()

    // The ledger's first row with a formula: its m/z cell opens it.
    const row = page
      .locator('tr')
      .filter({ has: page.getByRole('button', { name: 'Record a verdict' }) })
      .first()
    await expect(row).toBeVisible({ timeout: 30_000 })
    const formula = (await row.locator('td').nth(3).innerText()).trim()
    await row.locator('td').nth(1).click()

    const card = page.locator('section.inspector')
    const answer = card.getByTestId('answer')
    const why = card.getByTestId('why')
    const bar = card.getByTestId('verdict-bar')
    await expect(answer.locator('.insp-formula')).toHaveText(formula)

    // On the first screen: each is inside the window, not below it.
    for (const part of [answer, why, bar]) {
      await expect(part).toBeInViewport()
    }
    // The sections are folded, and the verdict bar is the card's foot.
    for (const toggle of await card.getByTestId('section-toggle').all()) {
      await expect(toggle).toHaveAttribute('aria-expanded', 'false')
    }
    await expect(
      bar.getByRole('button', { name: /Find other formulas for this peak/ })
    ).toBeVisible()
  })
})

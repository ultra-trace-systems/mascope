import { test, expect } from './fixtures'

/**
 * The Match tab is disabled until an ion's match is open, and says so on
 * hover. "Open match" (the ion's right-click menu, Enter on its row) opens one,
 * which enables the tab and brings it forward. A click on a row only selects
 * it, for the Batch chart.
 *
 * Reads the demo data and changes nothing, so it finds a sample with matched
 * ions through the API and opens that location by URL.
 */

/** The first sample of the demo data with a matched ion, and that ion. */
async function searchMatchedIon(api) {
  for (const workspace of (await api.get('/workspaces')).data) {
    const datasets = (await api.get(`/workspaces/${workspace.workspace_id}/datasets`)).data
    for (const dataset of datasets) {
      const batches = (await api.get(`/sample/batches?dataset_id=${dataset.dataset_id}`)).data
      for (const batch of batches) {
        const samples = (await api.get(`/samples?sample_batch_id=${batch.sample_batch_id}`)).data
        for (const sample of samples.slice(0, 3)) {
          const collections = (
            await api.get(`/match/records/collection?sample_item_id=${sample.sample_item_id}`)
          ).data
          for (const collection of collections) {
            const ions = (
              await api.post('/match/records/ion', {
                sample_item_ids: [sample.sample_item_id],
                target_collection_id: collection.target_collection_id
              })
            ).data
            // The table sorts by score, best first, so the best ion is the
            // first row and rendered whatever the virtual scroller shows.
            const best = [...ions].sort(
              (a, b) => (b.match?.match_score ?? 0) - (a.match?.match_score ?? 0)
            )[0]
            if (best?.match?.match_score > 0) {
              return { workspace, dataset, batch, sample, collection, ion: best }
            }
          }
        }
      }
    }
  }
  throw new Error('The demo data has no sample with a matched ion')
}

// Searched once per worker: the `api` fixture is per test, so not in beforeAll.
let matched
const findMatchedIon = (api) => (matched ??= searchMatchedIon(api))

/** Open the ion table of the found sample and collection, and return its best row. */
async function openIonTable(page, found) {
  const query = new URLSearchParams({
    w: found.workspace.workspace_id,
    d: found.dataset.dataset_id,
    b: found.batch.sample_batch_id,
    s: found.sample.sample_item_id,
    c: found.collection.target_collection_id,
    tab: 'sample',
    v: '1'
  })
  await page.goto(`/?${query}`)
  const row = page.locator('tr', { has: page.locator(`#target-ion-${found.ion.target_ion_id}`) })
  await expect(row).toBeVisible({ timeout: 30_000 })
  return row
}

// The tab's name starts with its icon's glyph.
const matchTab = (page) => page.getByRole('tab', { name: /Match$/ })

test.describe('opening a match', () => {
  test('the disabled Match tab says what enables it', async ({ page, api }) => {
    await openIonTable(page, await findMatchedIon(api))

    await expect(matchTab(page)).toHaveAttribute('aria-disabled', 'true')
    await matchTab(page).hover()
    await expect(page.getByRole('tooltip')).toHaveText('Open a match from the ion table')
  })

  test('"Open match" in the ion menu enables and activates the Match tab', async ({
    page,
    api
  }) => {
    const row = await openIonTable(page, await findMatchedIon(api))

    await row.click({ button: 'right' })
    await page.getByRole('menuitem', { name: 'Open match' }).click()

    await expect(matchTab(page)).not.toHaveAttribute('aria-disabled', 'true')
    await expect(matchTab(page)).toHaveAttribute('aria-selected', 'true')
  })

  test('Enter on a focused ion row opens its match', async ({ page, api }) => {
    const row = await openIonTable(page, await findMatchedIon(api))

    await row.focus()
    await page.keyboard.press('Enter')

    await expect(matchTab(page)).toHaveAttribute('aria-selected', 'true')
  })

  test('a click on an ion row only selects it', async ({ page, api }) => {
    const row = await openIonTable(page, await findMatchedIon(api))

    await row.click()

    await expect(row).toHaveAttribute('aria-selected', 'true')
    await expect(matchTab(page)).toHaveAttribute('aria-disabled', 'true')
  })
})

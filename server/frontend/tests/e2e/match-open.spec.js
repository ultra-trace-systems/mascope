import { test, expect } from './fixtures'

/**
 * The Match tab is disabled until an ion's match is open, and says so on
 * hover. "Open match" (the ion's right-click menu, Enter on its row) opens one,
 * which enables the tab and brings it forward. A click on a row only selects
 * it, for the Batch chart.
 *
 * Reads the demo data and changes nothing, so it finds a sample with matched
 * ions through the API and opens that location by URL.
 *
 * Opening a location by URL fills the ion table twice: first for the batch,
 * then, once the URL's sample is focused, for that sample. While the second
 * list loads, a "loading..." spinner is drawn over the table, which stays
 * mounted, so a focused row keeps its focus and an Enter still reaches it
 * (#3457; the last test). The other tests act on the row only once it shows
 * the sample's own match score, which the batch's list does not show for it.
 */

// A match score as the ion table's score tag shows it (BaseMatchTag.vue).
const matchScore = new Intl.NumberFormat('en-US', {
  style: 'percent',
  minimumIntegerDigits: 2,
  minimumFractionDigits: 2,
  maximumFractionDigits: 2
})

/**
 * The first sample of the demo data with a matched ion that another sample of
 * its batch matches better, that ion, and the ion's score in the sample.
 */
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
            if (!(best?.match?.match_score > 0)) continue
            // The batch's list shows each ion's best score in the batch. Only
            // an ion whose best is another sample's tells the two lists apart.
            const batchIons = (
              await api.post('/match/records/ion', {
                sample_batch_id: batch.sample_batch_id,
                target_collection_id: collection.target_collection_id
              })
            ).data
            const inBatch = batchIons.find((i) => i.target_ion_id === best.target_ion_id)
            const score = matchScore.format(best.match.match_score)
            if (inBatch && matchScore.format(inBatch.match?.match_score ?? 0) !== score) {
              return { workspace, dataset, batch, sample, collection, ion: best, score }
            }
          }
        }
      }
    }
  }
  throw new Error(
    'The demo data has no sample whose best matched ion matches better in another sample'
  )
}

// Searched once per worker: the `api` fixture is per test, so not in beforeAll.
let matched
const findMatchedIon = (api) => (matched ??= searchMatchedIon(api))

/** The shared link to the found sample's ion table. */
const locationOf = (found) =>
  `/?${new URLSearchParams({
    w: found.workspace.workspace_id,
    d: found.dataset.dataset_id,
    b: found.batch.sample_batch_id,
    s: found.sample.sample_item_id,
    c: found.collection.target_collection_id,
    tab: 'sample',
    v: '1'
  })}`

/**
 * Open the ion table of the found sample and collection, and return its best
 * row once the table lists the sample's ions, not the batch's.
 */
async function openIonTable(page, found) {
  await page.goto(locationOf(found))
  const row = page.locator('tr', { has: page.locator(`#target-ion-${found.ion.target_ion_id}`) })
  await expect(row.getByText(found.score, { exact: true })).toBeVisible({ timeout: 30_000 })
  return row
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

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

  // #3457: the sample's ion list reloads the table under a focused row. The
  // two delays hold the batch's list on screen first, then hold the sample's
  // long enough for the spinner to show. The panel keeps the table mounted
  // under it, so the row keeps focus and the Enter reaches it. Read as the
  // Match tab losing aria-disabled, not as aria-selected, which a shared
  // link's tab restore can still move (MAS-1155).
  test('Enter on a focused ion row during a reload opens its match', async ({ page, api }) => {
    const found = await findMatchedIon(api)

    await page.route(/\/api\/samples\?/, async (route) => {
      await sleep(2_000)
      await route.continue()
    })
    let sampleScopeSent
    const sampleScope = new Promise((resolve) => (sampleScopeSent = resolve))
    await page.route('**/api/match/records/ion', async (route) => {
      if (route.request().postDataJSON()?.sample_item_ids) {
        sampleScopeSent()
        await sleep(3_000)
      }
      await route.continue()
    })

    await page.goto(locationOf(found))
    await sampleScope

    // The ion panel, not the sample table's: the innermost panel with ion rows.
    // Held as the element, so the spinner is looked for in the same panel even
    // if the rows were to leave it.
    const ionPanel = page.locator('.browser:has([id^="target-ion-"]):not(:has(.browser))')
    await ionPanel.locator('tr[data-p-index="0"]').focus()
    const panel = await ionPanel.elementHandle()
    await panel.waitForSelector('.spinner', { state: 'attached' })
    await page.keyboard.press('Enter')

    await expect(matchTab(page)).not.toHaveAttribute('aria-disabled', 'true')
  })
})

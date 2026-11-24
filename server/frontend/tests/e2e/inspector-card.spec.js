import { test, expect, skipUnlessRequired } from './fixtures'

/**
 * The peak inspector answers before it explains
 * (docs/dev/assignment_ledger_and_card.md, section 4.1): at the column's
 * default width, on a 1280 px window, its first screen holds the answer, the
 * Why box and the verdict bar, whatever the folded sections below hold.
 *
 * It needs a sample with a completed assignment run, and the assignment views
 * switched on. A sample that already has a run is read as it is. Otherwise,
 * since the demo bundle ships no runs, it makes one: it copies a demo sample
 * into a batch of a scratch workspace and assigns the copy, so the demo data
 * stays as shipped and the run goes with the workspace after the test.
 *
 * It skips, saying why, where the views are off (the demo's default, and the
 * CI stack's first run, which switches assignment on in the backend only) or
 * where the backend refuses to assign. CI runs it a second time on a frontend
 * recreated with the views on (.github/docker-compose.demo.assignment.yaml),
 * with MASCOPE_E2E_REQUIRE_ASSIGNMENT set: there it fails where it would skip.
 */

/** How long one sample's run may take on the CI stack; about a minute there. */
const RUN_TIMEOUT = 360_000

/** Every demo batch, with where it is, in the order the API lists them. */
async function* demoBatches(api) {
  for (const workspace of (await api.get('/workspaces')).data) {
    const datasets = (await api.get(`/workspaces/${workspace.workspace_id}/datasets`)).data
    for (const dataset of datasets) {
      const batches = (await api.get(`/sample/batches?dataset_id=${dataset.dataset_id}`)).data
      for (const batch of batches) yield { workspace, dataset, batch }
    }
  }
}

/** The sample's runs, newest first. */
async function runsOf(api, sampleId) {
  const runs = await api.get(`/peak-assignments/sample/${sampleId}/runs`)
  return Array.isArray(runs) ? runs : (runs?.data ?? [])
}

/** The first sample on the stack with a completed assignment run, or null. */
async function findAssignedSample(api) {
  for await (const place of demoBatches(api)) {
    const samples = (await api.get(`/samples?sample_batch_id=${place.batch.sample_batch_id}`)).data
    for (const sample of samples.slice(0, 5)) {
      if ((await runsOf(api, sample.sample_item_id)).some((run) => run.status === 'completed')) {
        return { ...place, sample }
      }
    }
  }
  return null
}

/** The first demo sample, with where it is. */
async function findDemoSample(api) {
  for await (const place of demoBatches(api)) {
    const samples = (await api.get(`/samples?sample_batch_id=${place.batch.sample_batch_id}`)).data
    if (samples.length) return { ...place, sample: samples[0] }
  }
  throw new Error('The demo data has no sample')
}

/** Open the sample in the Sample tab; whether the assignment views show. */
async function openSample(page, target) {
  const query = new URLSearchParams({
    w: target.workspace.workspace_id,
    d: target.dataset.dataset_id,
    b: target.batch.sample_batch_id,
    s: target.sample.sample_item_id,
    tab: 'sample',
    v: '1'
  })
  await page.goto(`/?${query}`)
  return page
    .getByRole('group', { name: 'Targets or assignments' })
    .waitFor({ timeout: 30_000 })
    .then(() => true)
    .catch(() => false)
}

/**
 * A copy of the demo sample in a batch of the scratch workspace, assigned:
 * its run launched and waited for until it completed. Skips where the
 * backend refuses to assign (403): a stack whose frontend shows the views
 * while its backend has assignment off.
 */
async function assignedCopy(api, scratch, source) {
  const batch = await scratch.createBatch(`e2e-inspector-${scratch.id}`)
  await api.post('/sample/items/copy', {
    sample_item_ids: [source.sample.sample_item_id],
    sample_batch_id: batch.sample_batch_id
  })
  // The copy is a background task.
  let sample
  await expect
    .poll(
      async () => {
        sample = (await api.get(`/samples?sample_batch_id=${batch.sample_batch_id}`)).data[0]
        return Boolean(sample)
      },
      { timeout: 60_000 }
    )
    .toBe(true)

  const launch = await api.attempt(
    'POST',
    `/peak-assignments/sample/${sample.sample_item_id}/assign`
  )
  skipUnlessRequired(
    launch.status === 403,
    'Peak assignment is switched off in the backend of this stack'
  )
  expect(launch.status, `assign -> ${launch.status}: ${JSON.stringify(launch.body)}`).toBe(202)
  // The run exists before the 202 does, so its id is the one to follow.
  const runId = launch.body.data[0].peak_assignment_run_id

  await expect
    .poll(
      async () => {
        const run = (await runsOf(api, sample.sample_item_id)).find(
          (r) => r.peak_assignment_run_id === runId
        )
        if (run?.status === 'failed') throw new Error(`The copy's run failed: ${run.error}`)
        return run?.status
      },
      { timeout: RUN_TIMEOUT, intervals: [3_000] }
    )
    .toBe('completed')
  return { workspace: scratch.workspace, dataset: scratch.dataset, batch, sample }
}

test.describe('the peak inspector card', () => {
  test.use({ viewport: { width: 1280, height: 800 } })

  test('shows the answer, the Why box and the verdict bar on its first screen', async ({
    page,
    api,
    scratch
  }) => {
    // A run of its own takes about a minute on the CI stack, the copy and
    // the page around it the rest.
    test.setTimeout(RUN_TIMEOUT + 120_000)
    let target = await findAssignedSample(api)
    const source = target ?? (await findDemoSample(api))
    // Whether the views are on, before a run is made for nothing.
    const shown = await openSample(page, source)
    skipUnlessRequired(!shown, 'The assignment views are switched off on this stack')
    if (!target) {
      target = await assignedCopy(api, scratch, source)
      expect(await openSample(page, target)).toBe(true)
    }
    await page.getByText('Assignments', { exact: true }).first().click()

    // The ledger's first row with an assigned formula, the one the card
    // gives a Why box: its m/z cell opens it. Every row offers a verdict, the
    // reagent's and an unassigned peak's too, so the row is found by Curate,
    // which only a row with an assigned formula offers.
    const row = page
      .locator('tr')
      .filter({
        has: page.getByRole('button', { name: 'Curate: search other formulas for this peak' })
      })
      .first()
    await expect(row).toBeVisible({ timeout: 30_000 })
    // The formula itself, not the family's caret beside it nor the listed
    // name under it; and its m/z opens the peak in the inspector.
    const formula = (
      await row.getByTestId('compound-cell').locator('.field > span').first().innerText()
    ).trim()
    // The click focuses the row's peak, found in the sample's peak list. A
    // click before that list is in is dropped, and the row stays unselected
    // (#3588): on CI the ledger's rows can show first. So click until the row
    // holds the selection, and never on a selected row, which a click
    // unselects.
    await expect(async () => {
      if ((await row.getAttribute('aria-selected')) !== 'true') {
        await row.getByTestId('ledger-mz').click()
      }
      await expect(row).toHaveAttribute('aria-selected', 'true', { timeout: 2_000 })
    }).toPass({ timeout: 60_000 })

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

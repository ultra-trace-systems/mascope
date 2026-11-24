import { test, expect, skipUnlessRequired } from './fixtures'

/**
 * Assign batch: on a batch with samples and no batch peaks yet, the empty
 * ledger offers Assign batch, and once pressed the control in the switch bar
 * shows the pass running.
 *
 * Seeds its own batch: a scratch workspace whose batch gets two demo samples
 * copied in, so the demo data's own ledgers are left as they are. The pass is
 * cancelled and allowed to stop before the scratch workspace is deleted, so
 * nothing keeps writing after the test.
 *
 * The assignment views are the peak-assignment feature's. A stack whose
 * frontend runs with it off (the demo's default, and CI's demo stack, which
 * switches it on for the backend alone) shows no Targets/Assignments switch,
 * and the test skips there. CI runs it a second time on a frontend recreated
 * with the views on (.github/docker-compose.demo.assignment.yaml), with
 * MASCOPE_E2E_REQUIRE_ASSIGNMENT set: there it fails where it would skip.
 */

/** Two samples of the demo data, from the first batch that has two. */
async function findDemoSamples(api) {
  for (const workspace of (await api.get('/workspaces')).data) {
    const datasets = (await api.get(`/workspaces/${workspace.workspace_id}/datasets`)).data
    for (const dataset of datasets) {
      const batches = (await api.get(`/sample/batches?dataset_id=${dataset.dataset_id}`)).data
      for (const batch of batches) {
        const samples = (await api.get(`/samples?sample_batch_id=${batch.sample_batch_id}`)).data
        if (samples.length >= 2) return samples.slice(0, 2)
      }
    }
  }
  throw new Error('The demo data has no batch with two samples')
}

/** The batch's assignment status, as the control reads it. */
const statusOf = async (api, batchId) =>
  (await api.get(`/batch-peaks/batch/${batchId}/assignment-status`)).data

test.describe('Assign batch', () => {
  test('offers Assign batch on an empty ledger and shows the pass running', async ({
    page,
    api,
    scratch
  }) => {
    test.setTimeout(180_000)
    const samples = await findDemoSamples(api)
    const batch = await scratch.createBatch(`e2e-assign-${scratch.id}`)
    await api.post('/sample/items/copy', {
      sample_item_ids: samples.map((s) => s.sample_item_id),
      sample_batch_id: batch.sample_batch_id
    })
    // The copy is a background task.
    await expect
      .poll(
        async () =>
          (await api.get(`/samples?sample_batch_id=${batch.sample_batch_id}`)).data.length,
        { timeout: 60_000 }
      )
      .toBe(2)

    const query = new URLSearchParams({
      v: '1',
      w: scratch.workspace.workspace_id,
      d: scratch.dataset.dataset_id,
      b: batch.sample_batch_id,
      tab: 'batch'
    })
    await page.goto(`/?${query}`)
    const modeSwitch = page.getByRole('group', { name: 'Targets or assignments' })
    const switchShown = await modeSwitch
      .waitFor({ state: 'visible', timeout: 15_000 })
      .then(() => true)
      .catch(() => false)
    skipUnlessRequired(!switchShown, 'The assignment views are off on this stack')
    await modeSwitch.getByText('Assignments').click()

    const control = page.getByTestId('assign-status')
    try {
      // Where the batch stands: no batch peaks yet. The ledger carries the
      // button, and the bar hides its own copy.
      await expect(control).toContainText('Not assigned', { timeout: 30_000 })
      await expect(page.getByTestId('assign-main')).toHaveCount(0)
      const assign = page.getByRole('button', { name: 'Assign batch' })
      await expect(assign).toHaveCount(1)
      await expect(assign).toBeEnabled()

      const launched = page.waitForResponse((r) =>
        r.url().endsWith(`/batch-peaks/batch/${batch.sample_batch_id}/assign`)
      )
      await assign.click()
      expect((await launched).status()).toBe(202)

      // The pass runs: the control says so, and offers Cancel.
      await expect(control).toContainText('Assigning the batch', { timeout: 30_000 })
      await expect(page.getByTestId('assign-main')).toHaveText(/Cancel|Stopping/)
    } finally {
      // Stop it, and let it stop, before the workspace goes.
      const state = (await statusOf(api, batch.sample_batch_id)).state
      if (state === 'running') {
        await api
          .post(`/batch-peaks/batch/${batch.sample_batch_id}/assign/cancel`, {})
          .catch(() => null)
      }
      await expect
        .poll(async () => (await statusOf(api, batch.sample_batch_id)).state, {
          timeout: 120_000
        })
        .not.toBe('running')
    }
  })
})

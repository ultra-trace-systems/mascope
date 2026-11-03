import { test, expect } from './fixtures'

/**
 * The demo's MS2 files list their MS2 groups.
 *
 * The group inventory is written when a file's MS2 store is, so a demo
 * snapshot taken before the inventory existed restores stores it does not
 * list. The demo loader (tooling/demo-init.sh) lists them from the stores;
 * without that, the samples list, the batch MS2 groups and the sample detail
 * show no MS2. Reads the demo data and changes nothing.
 */

/** Every batch of the demo data. */
async function demoBatches(api) {
  const batches = []
  for (const workspace of (await api.get('/workspaces')).data) {
    const datasets = (await api.get(`/workspaces/${workspace.workspace_id}/datasets`)).data
    for (const dataset of datasets) {
      batches.push(...(await api.get(`/sample/batches?dataset_id=${dataset.dataset_id}`)).data)
    }
  }
  return batches
}

test('the demo samples with an MS2 store list their MS2 groups', async ({ api }) => {
  let listed = 0
  for (const batch of await demoBatches(api)) {
    const groups = (await api.get(`/sample/batches/${batch.sample_batch_id}/ms2/groups`)).data
    if (groups.length === 0) continue
    listed += 1
    // The samples list counts the same groups the batch lists
    const samples = (await api.get(`/samples?sample_batch_id=${batch.sample_batch_id}`)).data
    for (const sample of samples) {
      const own = groups.filter((group) => group.sample_item_id === sample.sample_item_id)
      expect(sample.ms2_group_count, `MS2 groups of a sample of ${batch.sample_batch_name}`).toBe(
        own.length
      )
    }
  }
  expect(listed, 'demo batches whose MS2 groups are listed').toBeGreaterThan(0)
})

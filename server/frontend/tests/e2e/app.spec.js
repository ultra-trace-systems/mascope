import { test, expect, env } from './fixtures'

test.describe('app shell (demo data)', () => {
  test('renders the dashboard for a signed-in user', async ({ page }) => {
    // The fixture waited for the instrument selector; assert the login
    // screen is gone and the dashboard landmarks are up.
    await expect(page.getByText('Sign-in to Mascope')).toHaveCount(0)
    await expect(page.locator('#instrument-selector')).toBeAttached()
  })

  test('identifies the signed-in user via the API cookie', async ({ api }) => {
    const me = (await api.get('/users/me')).data
    expect(me.email).toBe(env.email)
  })

  test('serves the seeded demo data through the API', async ({ api }) => {
    // Walk the data hierarchy: workspaces -> datasets -> batches. The demo has
    // more than one workspace (e.g. an empty "System Workspace") and more than
    // one dataset, and their order is not guaranteed, so aggregate across all of
    // them rather than assuming the first entry holds the data.
    // List endpoints respond with a { message, results, data } wrapper.
    const workspaces = (await api.get('/workspaces')).data
    expect(workspaces.length, 'demo dataset should contain a workspace').toBeGreaterThan(0)

    const datasets = (
      await Promise.all(
        workspaces.map((w) =>
          api.get(`/workspaces/${w.workspace_id}/datasets`).then((r) => r.data)
        )
      )
    ).flat()
    expect(datasets.length, 'demo should contain a dataset').toBeGreaterThan(0)

    const batchCounts = await Promise.all(
      datasets.map((d) =>
        api.get(`/sample/batches?dataset_id=${d.dataset_id}`).then((r) => r.data.length)
      )
    )
    const totalBatches = batchCounts.reduce((sum, n) => sum + n, 0)
    expect(totalBatches, 'demo dataset should contain a sample batch').toBeGreaterThan(0)
  })
})

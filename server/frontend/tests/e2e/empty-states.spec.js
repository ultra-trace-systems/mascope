import { test, expect, openWorkspace } from './fixtures'

// An empty list says what it is for and offers the first item, and its
// create button in the panel header spells out its label instead of a lone
// "+" (the label hides again once the list has rows).

test.describe('empty states', () => {
  test('an empty workspace offers its first dataset', async ({ page, api, scratch }) => {
    // The scratch workspace already holds the scratch dataset, so the empty
    // list needs a workspace of its own; it goes with the test.
    const workspace = (
      await api.post('/workspaces', { workspace_name: `e2e-empty-ws-${scratch.id}` })
    ).data
    try {
      await openWorkspace(page, workspace.workspace_name)

      await expect(page.getByText('No datasets yet')).toBeVisible()
      await expect(page.getByRole('button', { name: 'Create the first dataset' })).toBeVisible()
      const create = page.getByRole('button', { name: 'Create dataset', exact: true })
      await expect(create.getByText('Create dataset')).toBeVisible()

      // No batch is open, so the collection pane is headed by its name and
      // says how it fills.
      await expect(page.getByText('Select a batch to see its target collections')).toBeVisible()
    } finally {
      await api.delete(`/workspaces/${workspace.workspace_id}`)
    }
  })

  test('an empty dataset offers its first batch', async ({ page, scratch }) => {
    await openWorkspace(page, scratch.workspace.workspace_name)
    await page.getByRole('row', { name: new RegExp(scratch.dataset.dataset_name) }).click()

    await expect(page.getByText('No batches yet')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Create the first batch' })).toBeVisible()
    const create = page.getByRole('button', { name: 'Create batch', exact: true })
    await expect(create.getByText('Create batch')).toBeVisible()

    // The datasets beside it are listed, so their create button is the icon.
    const createDataset = page.getByRole('button', { name: 'Create dataset', exact: true })
    await expect(createDataset.getByText('Create dataset')).toBeHidden()
  })
})

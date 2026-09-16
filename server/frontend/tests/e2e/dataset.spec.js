import { test, expect, openWorkspace } from './fixtures'

test.describe('dataset ops', () => {
  test('create dataset', async ({ page, scratch }) => {
    const name = `e2e-created-ds-${scratch.id}`
    await openWorkspace(page, scratch.workspace.workspace_name)

    await page.getByRole('button', { name: 'Create dataset' }).click()
    const dialog = page.getByRole('dialog', { name: 'Create a new dataset' })
    await dialog.getByRole('textbox', { name: 'Name' }).fill(name)
    await dialog.getByRole('button', { name: 'Create' }).click()

    // the new dataset is opened: the top bar names it and its batches show
    await expect(page.locator('menu.breadcrum').getByRole('button', { name })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Create batch' })).toBeVisible()
  })

  test('rename dataset', async ({ page, scratch }) => {
    const renamed = `e2e-renamed-ds-${scratch.id}`
    await openWorkspace(page, scratch.workspace.workspace_name)

    await page
      .getByRole('row', { name: new RegExp(scratch.dataset.dataset_name) })
      .click({ button: 'right' })
    await page.getByRole('menuitem', { name: 'Edit dataset' }).click()
    const dialog = page.getByRole('dialog', { name: /Edit dataset/ })
    await dialog.getByRole('textbox', { name: 'Name' }).fill(renamed)
    await dialog.getByRole('button', { name: 'Save' }).click()

    await expect(page.getByRole('row', { name: new RegExp(renamed) })).toBeVisible()
    await expect(
      page.getByRole('row', { name: new RegExp(`${scratch.dataset.dataset_name}$`) })
    ).toHaveCount(0)
  })

  test('delete dataset', async ({ page, scratch }) => {
    await openWorkspace(page, scratch.workspace.workspace_name)

    const row = page.getByRole('row', { name: new RegExp(scratch.dataset.dataset_name) })
    await row.click({ button: 'right' })
    await page.getByRole('menuitem', { name: 'Delete dataset' }).click()
    const dialog = page.getByRole('dialog', { name: /Delete dataset/ })
    await dialog.getByRole('button', { name: 'Delete' }).click()

    await expect(row).toHaveCount(0)
  })
})

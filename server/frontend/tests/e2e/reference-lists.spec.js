import { test, expect } from './fixtures'

// Settings opens the Reference lists dialog: the lists the deployment has
// loaded, read only, with a list's species a page at a time. The demo seed
// loads the lists Mascope ships by default, the monoterpene HOM list among
// them.

const HOM_LABEL = 'Monoterpene OH-oxidation HOM'
const HOM_SPECIES = 573

test.describe('reference lists', () => {
  test('Settings lists the loaded lists and pages through a list species', async ({ page }) => {
    const drawer = page.locator('.p-drawer')
    if (!(await drawer.isVisible())) {
      await page.locator('menu.breadcrum button').first().click()
    }
    await drawer
      .getByRole('tab')
      .filter({ has: page.locator('.ph-gear-six') })
      .click()
    await drawer.getByRole('button', { name: 'Reference lists' }).click()

    const dialog = page.locator('.p-dialog').filter({ hasText: 'Reference lists' })
    await expect(dialog).toBeVisible()
    const row = dialog.getByRole('row').filter({ hasText: HOM_LABEL })
    await expect(row).toHaveCount(1)
    await expect(row.getByRole('cell', { name: String(HOM_SPECIES), exact: true })).toBeVisible()

    await row.locator('button.p-datatable-row-toggle-button').click()
    await expect(dialog.getByText(`1-25 of ${HOM_SPECIES}`)).toBeVisible()
    await dialog.getByRole('button', { name: 'Next species page' }).click()
    await expect(dialog.getByText(`26-50 of ${HOM_SPECIES}`)).toBeVisible()
  })
})

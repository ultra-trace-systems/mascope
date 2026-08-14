import { test as base, expect } from '@playwright/test'

import { env } from './fixtures/env'

/**
 * A newly created account is held at the mandatory password screen until it
 * sets its own password.
 *
 * This spans a database column, an API dependency, an axios interceptor and a
 * template branch, so a unit test cannot show that the pieces line up. What it
 * proves that nothing else can: after the change, the app actually loads -
 * meaning the requirement cleared server-side and the login callbacks that
 * populate the stores fired.
 *
 * Two constraints shape it:
 *
 * - It never touches the demo account. The published demo password cannot pass
 *   the password policy (the user name is a substring of it), so a changed demo
 *   password could not be set back through the API, and every other spec
 *   depends on it.
 * - It cannot use the repo's `page` fixture, which waits up to 30 s for
 *   `#instrument-selector`. An account at the password screen never attaches
 *   that element, so the fixture would time out before the test began.
 */

// PrimeVue may place the id on the input or on a wrapper element.
const field = (page, id) => page.locator(`input#${id}, #${id} input`).first()

const throwaway = () => {
  const suffix = Math.random().toString(36).slice(2, 8)
  return {
    email: `pwgate-${suffix}@example.com`,
    username: `pwgate ${suffix}`,
    // Long enough for the policy, not a common password, and sharing nothing
    // with the identifiers above.
    password: 'nine bushels of slate',
    newPassword: 'sixteen tonnes of quartz'
  }
}

const test = base.extend({
  api: async ({ playwright }, use) => {
    const context = await playwright.request.newContext({
      baseURL: env.apiURL,
      storageState: env.storageStatePath
    })
    await use(context)
    await context.dispose()
  }
})

test.describe('mandatory password change', () => {
  let createdUserId = null

  test.afterEach(async ({ api }) => {
    if (createdUserId !== null) {
      await api.delete(`/api/users/owner/${createdUserId}`)
      createdUserId = null
    }
  })

  test('a new account must set its own password before using the app', async ({ api, browser }) => {
    // The demo account is an owner, so it can register users. A new account is
    // required to change its password on creation, which is the state under
    // test - no extra call needed to arrange it.
    test.setTimeout(90_000)
    const account = throwaway()
    const created = await api.post('/api/users/owner/register', {
      data: {
        email: account.email,
        username: account.username,
        password: account.password,
        role_id: 100
      }
    })
    expect(created.ok(), `register failed: ${await created.text()}`).toBeTruthy()
    createdUserId = (await created.json()).data.id

    // A clean context: this account, not the stored demo session.
    const context = await browser.newContext({ storageState: { cookies: [], origins: [] } })
    const page = await context.newPage()
    try {
      await page.goto('/')
      await field(page, 'login-email').fill(account.email)
      await field(page, 'login-password').fill(account.password)
      await page.getByRole('button', { name: 'Login' }).click()

      // Signed in, but held out of the app.
      await expect(page.getByText('Set a new password')).toBeVisible({ timeout: 30_000 })
      await expect(page.locator('#instrument-selector')).toHaveCount(0)

      await field(page, 'current-password').fill(account.password)
      await field(page, 'new-password').fill(account.newPassword)
      await field(page, 'new-password-verify').fill(account.newPassword)
      await page.getByRole('button', { name: 'Set password' }).click()

      // The app loads: the requirement cleared server-side and the stores that
      // register a login callback ran.
      await expect(page.locator('#instrument-selector')).toBeAttached({ timeout: 30_000 })
      await expect(page.getByText('Set a new password')).toBeHidden()
    } finally {
      await context.close()
    }
  })
})

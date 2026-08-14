import { test as setup, expect } from '@playwright/test'

import { env } from '../fixtures/env'

/**
 * Signs in once via the REST API and persists the resulting cookie state.
 * The e2e project depends on this and reuses the state via storageState,
 * so individual tests never go through the login UI.
 */
setup('authenticate via API', async ({ request }) => {
  const response = await request.post(`${env.apiURL}/api/auth/login`, {
    form: {
      grant_type: 'password',
      username: env.email,
      password: env.password
    }
  })
  expect(
    response.ok(),
    `login as ${env.email} failed (${response.status()}) - is the stack up at ${env.apiURL}?`
  ).toBeTruthy()

  // Sanity check: the cookie actually authenticates.
  const me = await request.get(`${env.apiURL}/api/users/me`)
  expect(me.ok()).toBeTruthy()

  // An account required to change its password is held at a password screen,
  // so every spec would fail on a missing dashboard rather than on the cause.
  // The demo seed pins this off; assert it here so a regression says so.
  const profile = (await me.json()).data
  expect(
    profile.must_change_password,
    `${env.email} is required to change its password, so the app will hold it at ` +
      'the password screen. Re-run the demo seed to release it.'
  ).toBeFalsy()

  await request.storageState({ path: env.storageStatePath })
})

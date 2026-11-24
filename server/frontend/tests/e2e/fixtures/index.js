import { test as base, expect } from '@playwright/test'

import { env } from './env'

/**
 * Base test for the hermetic e2e suite.
 *
 * - `page` arrives already signed in (cookie state from setup/auth.setup.js,
 *   wired via storageState in playwright.config.js) and navigated to the app.
 * - `api` is an authenticated helper for seeding and cleaning up state through
 *   the REST API. Prefer it over UI interactions for anything that is setup
 *   rather than the behavior under test: API seeding is fast and deterministic,
 *   UI seeding is where the flakiness lives.
 */
export const test = base.extend({
  page: async ({ page }, use) => {
    await page.goto('/')
    // #app is the always-present Vue mount div, so wait for a dashboard
    // landmark instead: the instrument selector only renders once the
    // authenticated app shell is up.
    await page.locator('#instrument-selector').waitFor({ state: 'attached', timeout: 30_000 })
    await use(page)
  },

  api: async ({ playwright }, use) => {
    const context = await playwright.request.newContext({
      baseURL: env.apiURL,
      storageState: env.storageStatePath
    })

    const raise = async (response, method, path) => {
      if (!response.ok()) {
        throw new Error(`${method} ${path} -> ${response.status()}: ${await response.text()}`)
      }
      return response
    }

    const api = {
      /** GET an /api path and return parsed JSON. */
      get: async (path) => (await raise(await context.get(`/api${path}`), 'GET', path)).json(),
      /** POST JSON to an /api path and return parsed JSON (null for empty bodies). */
      post: async (path, data) => {
        const response = await raise(await context.post(`/api${path}`, { data }), 'POST', path)
        const text = await response.text()
        return text ? JSON.parse(text) : null
      },
      /** PATCH JSON to an /api path. */
      patch: async (path, data) =>
        (await raise(await context.patch(`/api${path}`, { data }), 'PATCH', path)).json(),
      /** DELETE an /api path (used by cleanup hooks). */
      delete: async (path) => raise(await context.delete(`/api${path}`), 'DELETE', path),
      /** Send a request and return `{ status, body }` whatever the status, for
       *  a test that reads a refusal (a 403, a 409) rather than failing on it. */
      attempt: async (method, path, data) => {
        const response = await context.fetch(`/api${path}`, { method, data })
        const text = await response.text()
        const parse = () => {
          try {
            return text ? JSON.parse(text) : null
          } catch {
            return text
          }
        }
        return { status: response.status(), body: parse() }
      }
    }

    await use(api)
    await context.dispose()
  },

  /**
   * A scratch workspace + dataset seeded through the API for tests that
   * mutate data. Deleted (with everything inside, the API cascades) after
   * the test, so the demo stack stays reusable across runs.
   */
  scratch: async ({ api }, use) => {
    const id = Math.random().toString(36).slice(2, 8)
    const workspace = (await api.post('/workspaces', { workspace_name: `e2e-ws-${id}` })).data
    const dataset = (
      await api.post(`/workspaces/${workspace.workspace_id}/datasets`, {
        dataset_name: `e2e-ds-${id}`
      })
    ).data

    await use({
      id,
      workspace,
      dataset,
      createBatch: async (name, targetCollectionIds = []) =>
        (
          await api.post('/sample/batches', {
            sample_batch_name: name,
            dataset_id: dataset.dataset_id,
            target_collection_ids: targetCollectionIds
          })
        ).data
    })

    await api.delete(`/workspaces/${workspace.workspace_id}`)
  }
})

/**
 * Drill into a workspace from the workspace pane. Reloads first so state
 * seeded through the API after page load is visible in the list.
 */
export async function openWorkspace(page, name) {
  await page.reload()
  await page.locator('#instrument-selector').waitFor({ state: 'attached', timeout: 30_000 })
  await page.getByRole('option', { name }).click()
}

/**
 * Skip the test, saying why, where `condition` holds: or fail it, with the
 * same reason, where MASCOPE_E2E_REQUIRE_ASSIGNMENT is set (`env.requireAssignment`).
 * For the specs that need the assignment views and what they read, which a
 * stack may lack: skipped on the plain demo stack, required on CI's second
 * run, which is there to run them.
 */
export function skipUnlessRequired(condition, reason) {
  if (!condition) return
  if (env.requireAssignment) {
    throw new Error(
      `${reason}; MASCOPE_E2E_REQUIRE_ASSIGNMENT is set, so this fails instead of skipping`
    )
  }
  test.skip(true, reason)
}

export { expect, env }

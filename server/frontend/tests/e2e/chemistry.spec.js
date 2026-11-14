import { existsSync, copyFileSync, mkdtempSync, readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { test, expect, openWorkspace } from './fixtures/index.js'

/**
 * Choose chemistry offers the profiles Mascope ships, from the first start.
 *
 * A profile is listed whether or not anything was processed under it, so a
 * file that waits for a chemistry can be given one in a click, with no mode
 * built by hand. The demo's own modes, the ones its file names carry the
 * tokens of, are still offered, under Advanced.
 *
 * The second test uploads a copy of a demo raw file with its token taken out
 * of its name, so that it waits as "Needs a chemistry", chooses a profile for
 * it and waits for it to be processed. That files a sample in a daily batch
 * of the profile's, which the demo has none of. The SDK and AI contract tests
 * that run against this stack after the suite read the demo's batches, so
 * the test deletes the batch and the file again, whatever its outcome.
 */

const HERE = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.resolve(HERE, '../../../..')

async function openRawFilesTab(page) {
  const tab = page.getByRole('tab', { name: 'Raw files' })
  await tab.waitFor({ state: 'visible' })
  if ((await tab.getAttribute('aria-selected')) !== 'true') {
    await tab.click()
  }
}

/**
 * Raw files of every time, narrowed to names holding `search`.
 *
 * A fresh page asks for a workspace first, in a dialog over the tabs, so the
 * acquisition workspace of the file's instrument is opened. The search
 * narrows only the page on screen, so a file has to be on the first page:
 * the newest, or one the status filter, which narrows the query itself,
 * picks out.
 */
async function showRawFiles(page, file, search, status = null) {
  await openWorkspace(page, `Acquisitions ${file.instrument}`)
  await openRawFilesTab(page)
  await page.locator('#min-datetime').fill('2000/01/01 00:00')
  await page.locator('#min-datetime').press('Enter')
  if (status) {
    await page.getByLabel('Processing status').click()
    await page.getByRole('option', { name: status, exact: true }).click()
  }
  if (search) await page.getByPlaceholder('Search filenames').fill(search)
}

/** The texts of an open Select's list, group headers and options in order. */
async function selectEntries(page) {
  const list = page.locator('.p-select-overlay .p-select-list')
  await list.waitFor({ state: 'visible' })
  return list.locator('li').allInnerTexts()
}

/**
 * The directories the demo raw files are kept in: beside the upload spec's
 * file when one is named, and the local bundle cache.
 */
function rawDirectories() {
  const dirs = []
  const named = process.env.MASCOPE_E2E_RAW_FILE
  if (named && existsSync(named)) dirs.push(path.dirname(named))
  const cacheRoot = path.join(REPO_ROOT, '.runtime', 'demo')
  if (existsSync(cacheRoot)) {
    for (const version of readdirSync(cacheRoot).sort().reverse()) {
      const dir = path.join(cacheRoot, version, 'raw')
      if (existsSync(dir)) dirs.push(dir)
    }
  }
  return dirs
}

/** A raw file whose name carries `token`, or null. */
function rawCarrying(token) {
  for (const dir of rawDirectories()) {
    const raw = readdirSync(dir)
      .filter((name) => name.toLowerCase().endsWith('.raw') && name.includes(token))
      .sort()[0]
    if (raw) return path.join(dir, raw)
  }
  return null
}

const shared = (a, b) =>
  a.ionization_mechanism_ids.filter((id) => b.ionization_mechanism_ids.includes(id)).length

test.describe('chemistry profiles', () => {
  test('Choose chemistry offers the shipped profiles, and the demo own modes under Advanced', async ({
    page,
    api
  }) => {
    const modes = (await api.get('/ionization/modes')).data
    // Nothing was processed under them and nobody adopted one: listed anyway.
    const profiles = modes.filter((mode) => mode.system_key && mode.calibration_collection_id)
    const own = modes.filter((mode) => !mode.system_key && mode.ionization_mode_polarity === '-')
    expect(profiles.length).toBeGreaterThan(0)
    expect(own.length).toBeGreaterThan(0)
    const negative = profiles.filter((mode) => mode.ionization_mode_polarity === '-')
    expect(negative.some((mode) => mode.routed_files === 0)).toBe(true)

    // The newest negative demo file, which the first page of Raw files
    // holds. The dialog is opened and cancelled, so nothing is processed.
    const { data: files } = await api.get(
      '/sample/files?datetime_min=2000-01-01T00:00:00Z&limit=1000'
    )
    const file = files
      .filter((f) => f.polarity === '-')
      .sort((a, b) => b.datetime_utc.localeCompare(a.datetime_utc))[0]
    test.skip(!file, 'the demo dataset holds no negative file')
    await showRawFiles(page, file, file.filename)
    const row = page.getByRole('cell', { name: file.filename })
    await expect(row).toBeVisible({ timeout: 60_000 })
    await row.click({ button: 'right' })
    await page.getByRole('menuitem', { name: 'Choose chemistry' }).click()

    const dialog = page.locator('.p-dialog').filter({ hasText: 'Choose chemistry' })
    await expect(dialog).toBeVisible()
    await dialog.locator('#chemistry-negative').click()
    const entries = await selectEntries(page)

    const profilesAt = entries.indexOf('Profiles')
    const advancedAt = entries.indexOf('Advanced')
    expect(profilesAt).toBe(0)
    expect(advancedAt).toBeGreaterThan(profilesAt)
    for (const profile of negative) {
      const at = entries.indexOf(profile.ionization_mode_name)
      expect(at, profile.ionization_mode_name).toBeGreaterThan(profilesAt)
      expect(at, profile.ionization_mode_name).toBeLessThan(advancedAt)
    }
    for (const mode of own) {
      const label = mode.ionization_mode_token
        ? `${mode.ionization_mode_name} (${mode.ionization_mode_token})`
        : mode.ionization_mode_name
      expect(entries.indexOf(label), label).toBeGreaterThan(advancedAt)
    }

    await page.keyboard.press('Escape')
    await dialog.getByRole('button', { name: 'Cancel' }).click()
    await expect(dialog).toBeHidden()
  })

  test('choosing a profile for a parked file processes it', async ({ page, api }) => {
    const modes = (await api.get('/ionization/modes')).data
    // A demo mode whose token a demo raw file carries, and the profile that
    // shares the most of its mechanisms: the chemistry the file was run under.
    const tokened = modes.filter(
      (mode) =>
        !mode.system_key && mode.ionization_mode_token && mode.ionization_mode_polarity === '-'
    )
    const own = tokened.find((mode) => rawCarrying(mode.ionization_mode_token))
    test.skip(
      !own,
      'no demo raw file carries a negative mode token; set MASCOPE_E2E_RAW_FILE or fetch the demo bundle'
    )
    const source = rawCarrying(own.ionization_mode_token)
    const profile = modes
      .filter(
        (mode) =>
          mode.system_key && mode.calibration_collection_id && mode.ionization_mode_polarity === '-'
      )
      .sort((a, b) => shared(b, own) - shared(a, own))[0]
    // Real conversion, then processing under the profile, takes minutes.
    test.setTimeout(15 * 60_000)

    // The demo name less every configured token, and a token of the test's
    // own so the stored name cannot collide with the demo's.
    const id = Math.random().toString(36).slice(2, 8)
    const marker = `e2e${id}`
    let name = path.basename(source, '.raw')
    for (const mode of modes) {
      if (mode.ionization_mode_token) name = name.split(mode.ionization_mode_token).join('')
    }
    const uploadName = `${name.replace(/_+/g, '_').replace(/_$/, '')}_${marker}.raw`
    const staged = path.join(mkdtempSync(path.join(tmpdir(), 'mascope-e2e-')), uploadName)
    copyFileSync(source, staged)

    let sampleFile
    try {
      // --- Upload, as the upload spec does ---
      await openRawFilesTab(page)
      await page.locator('#uppy-upload-trigger').dispatchEvent('click')
      await page.locator('.uppy-Dashboard-input').first().setInputFiles(staged)
      const uploadButton = page.locator('.uppy-StatusBar-actionBtn--upload')
      await uploadButton.waitFor({ state: 'visible' })
      await uploadButton.dispatchEvent('click')

      // --- It waits for a chemistry: its name names none ---
      await expect
        .poll(
          async () => {
            const { data } = await api.get(
              '/sample/files?datetime_min=2000-01-01T00:00:00Z&limit=1000'
            )
            sampleFile = data.find((f) => f.filename.includes(marker))
            return sampleFile?.processing_status
          },
          { timeout: 6 * 60_000, intervals: [5_000], message: 'the upload parked' }
        )
        .toBe('needs_chemistry')

      // --- Choose the profile for it ---
      await showRawFiles(page, sampleFile, marker, 'Needs a chemistry')
      const row = page.getByRole('cell', { name: new RegExp(marker) })
      await expect(row).toBeVisible({ timeout: 60_000 })
      await row.click({ button: 'right' })
      await page.getByRole('menuitem', { name: 'Choose chemistry' }).click()
      const dialog = page.locator('.p-dialog').filter({ hasText: 'Choose chemistry' })
      await dialog.locator('#chemistry-negative').click()
      await page
        .locator('.p-select-overlay')
        .getByRole('option', { name: profile.ionization_mode_name, exact: true })
        .click()
      await dialog.getByRole('button', { name: 'Process' }).click()
      await expect(dialog).toBeHidden()

      // --- Processed under it, and its status names the profile ---
      await expect
        .poll(async () => (await api.get(`/sample/files/${sampleFile.sample_file_id}`)).data, {
          timeout: 6 * 60_000,
          intervals: [5_000],
          message: 'processed under the profile'
        })
        .toMatchObject({ processing_status: 'done' })
      const processed = (await api.get(`/sample/files/${sampleFile.sample_file_id}`)).data
      expect(processed.processing_detail).toContain(
        `Bound to the profile '${profile.ionization_mode_name}' (-) as chosen by hand.`
      )
      const listed = (await api.get('/ionization/modes')).data.find(
        (mode) => mode.ionization_mode_id === profile.ionization_mode_id
      )
      expect(listed.routed_files).toBe(1)
    } finally {
      if (sampleFile) await removeFile(api, sampleFile, profile)
    }
  })
})

/**
 * Delete the profile's daily batch the file was filed in, then the file.
 *
 * A batch is deleted in the background, and a file only once nothing holds
 * it, so the file's delete is asked again until it goes.
 */
async function removeFile(api, sampleFile, profile) {
  const batchName = `${sampleFile.datetime.slice(0, 10)} ${profile.ionization_mode_name} acquisition`
  const { data: workspaces } = await api.get('/workspaces')
  for (const workspace of workspaces) {
    const { data: datasets } = await api.get(`/workspaces/${workspace.workspace_id}/datasets`)
    for (const dataset of datasets ?? []) {
      const { data: batches } = await api.get(
        `/sample/batches?dataset_id=${dataset.dataset_id}&sample_batch_name=${encodeURIComponent(batchName)}`
      )
      for (const batch of batches ?? []) {
        if (batch.sample_batch_name === batchName) {
          await api.delete(`/sample/batches/${batch.sample_batch_id}`)
        }
      }
    }
  }
  await expect
    .poll(
      async () => {
        try {
          const { data } = await api.post('/sample/files/delete', {
            sample_file_ids: [sampleFile.sample_file_id]
          })
          return data.deleted.some((f) => f.sample_file_id === sampleFile.sample_file_id)
        } catch {
          // 422 while the batch's delete is still running: it holds the file.
          return false
        }
      },
      { timeout: 2 * 60_000, intervals: [5_000], message: 'the test file deleted' }
    )
    .toBe(true)
}

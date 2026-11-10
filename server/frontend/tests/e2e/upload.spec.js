import { existsSync, copyFileSync, mkdtempSync, readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { test, expect } from './fixtures/index.js'

/**
 * Upload -> process -> browse: the customer front door.
 *
 * Uploads a real demo raw file through the Uppy dashboard (tus endpoint),
 * waits for the file-converter to ingest it through the real conversion ->
 * peak detection -> matching pipeline, and browses the result in the
 * Raw files tab. Deep numeric verification of the pipeline output belongs to
 * the golden reproducibility test; this spec owns the UI journey.
 *
 * The raw file source is resolved from MASCOPE_E2E_RAW_FILE, falling back to
 * the local demo bundle cache (.runtime/demo/<version>/raw). The upload is
 * renamed with a unique token: the converter derives the canonical stored
 * filename from the upload name (instrument prefix + embedded acquisition
 * timestamp), so re-uploading a bundle file under its original name would
 * collide with the already-ingested demo data.
 */

const HERE = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.resolve(HERE, '../../../..')

/** Every sample file the demo user can see, the demo's past acquisitions included. */
const FILES_QUERY = '/sample/files?datetime_min=2000-01-01T00:00:00Z&limit=1000'

/** The processing statuses a file ends on (ProcessingStatus in the backend). */
const PROCESSING_ENDED = new Set(['needs_chemistry', 'calibration_failed', 'done', 'failed'])

/**
 * Make sure the Raw files tab is active. It is the default tab, so usually
 * no click is needed - and clicking blindly is flaky when a notification
 * toast overlaps the tab strip.
 */
async function openRawFilesTab(page) {
  const tab = page.getByRole('tab', { name: 'Raw files' })
  await tab.waitFor({ state: 'visible' })
  if ((await tab.getAttribute('aria-selected')) !== 'true') {
    await tab.click()
  }
}

/** Resolve a source .raw file: explicit env var, or the local bundle cache. */
function findSourceRaw() {
  const fromEnv = process.env.MASCOPE_E2E_RAW_FILE
  if (fromEnv) return existsSync(fromEnv) ? fromEnv : null

  const cacheRoot = path.join(REPO_ROOT, '.runtime', 'demo')
  if (!existsSync(cacheRoot)) return null
  // Newest bundle first; then the last file in name order. Sorted, so every
  // machine re-ingests the same file: readdirSync lists entries in the
  // filesystem's order. On the demo bundle the last name is a late
  // acquisition, which the Raw files tab lists on its first page, the only
  // one its filename search reads.
  const byName = (a, b) => a.localeCompare(b, 'en', { numeric: true })
  for (const version of readdirSync(cacheRoot).sort(byName).reverse()) {
    const rawDir = path.join(cacheRoot, version, 'raw')
    if (!existsSync(rawDir)) continue
    const raw = readdirSync(rawDir)
      .filter((f) => f.toLowerCase().endsWith('.raw'))
      .sort(byName)
      .at(-1)
    if (raw) return path.join(rawDir, raw)
  }
  return null
}

/** Every sample batch, across all workspaces and their datasets. */
async function listBatches(api) {
  const workspaces = (await api.get('/workspaces')).data
  const datasets = (
    await Promise.all(
      workspaces.map((w) => api.get(`/workspaces/${w.workspace_id}/datasets`).then((r) => r.data))
    )
  ).flat()
  return (
    await Promise.all(
      datasets.map((d) => api.get(`/sample/batches?dataset_id=${d.dataset_id}`).then((r) => r.data))
    )
  ).flat()
}

/**
 * Remove what the upload added: its samples, a batch it alone made, and the
 * file. The SDK and AI contract tests run on the same stack after this
 * suite, and read the demo batches: a re-ingested uronium file left in the
 * uronium daily batch moves the time span AI golden M5 expects (#2959).
 */
async function removeUpload(api, { token, batchIdsBefore }) {
  let file = (await api.get(FILES_QUERY)).data?.find((f) => f.filename.includes(token))
  if (!file) return // never registered: nothing reached the database

  // Deleting samples under a running pipeline races its later stages, so
  // wait for processing to end first.
  await expect
    .poll(
      async () => {
        file = (await api.get(FILES_QUERY)).data?.find((f) => f.filename.includes(token))
        return PROCESSING_ENDED.has(file?.processing_status)
      },
      { timeout: 5 * 60_000, intervals: [5_000], message: 'processing of the upload to end' }
    )
    .toBe(true)

  const holders = []
  for (const batch of await listBatches(api)) {
    const items = (
      await api.get(
        `/sample/items?sample_batch_id=${batch.sample_batch_id}&sample_file_id=${file.sample_file_id}`
      )
    ).data
    if (items.length) holders.push({ batch, items })
  }
  const itemIds = holders.flatMap(({ items }) => items.map((i) => i.sample_item_id))
  if (itemIds.length) await api.post('/sample/items/delete', { sample_item_ids: itemIds })
  for (const { batch } of holders) {
    if (!batchIdsBefore.has(batch.sample_batch_id)) {
      await api.delete(`/sample/batches/${batch.sample_batch_id}`)
    }
  }
  await api.post('/sample/files/delete', { sample_file_ids: [file.sample_file_id] })

  // A cleanup that stops working fails here, not in the contract tests
  // later: the batches are the ones from before the upload, no more and no
  // fewer. The suite runs on one worker, so no other test adds or removes
  // one meanwhile; a batch is deleted in the background.
  await expect
    .poll(async () => (await listBatches(api)).map((b) => b.sample_batch_id).sort(), {
      timeout: 60_000,
      intervals: [2_000],
      message: 'the batches from before the upload'
    })
    .toEqual([...batchIdsBefore].sort())
  const files = (await api.get(FILES_QUERY)).data ?? []
  expect(files.some((f) => f.filename.includes(token))).toBe(false)
}

test.describe('sample file upload', () => {
  // What the running test uploaded, for afterEach to remove. Set before the
  // upload starts, so a test that fails after it still cleans up.
  let upload = null

  test.afterEach(async ({ api }) => {
    if (!upload) return
    const made = upload
    upload = null
    await removeUpload(api, made)
  })

  test('uploads a raw file and the pipeline processes it into a browsable sample', async ({
    page,
    api
  }) => {
    const source = findSourceRaw()
    test.skip(
      !source,
      'no demo raw file available; set MASCOPE_E2E_RAW_FILE or fetch the demo bundle (mascope demo fetch)'
    )
    // Real conversion + peak detection + matching takes a few minutes, and
    // the cleanup waits for it to end. afterEach shares this timeout.
    test.setTimeout(15 * 60_000)

    // Stage a uniquely named copy: keep the instrument prefix (first token)
    // and the ionization token so upload validation passes, append an e2e
    // token so the canonical stored filename cannot collide with demo data.
    const id = Math.random().toString(36).slice(2, 8)
    const token = `e2e${id}`
    const uploadName = `${path.basename(source, '.raw')}_${token}.raw`
    const staged = path.join(mkdtempSync(path.join(tmpdir(), 'mascope-e2e-')), uploadName)
    copyFileSync(source, staged)
    upload = {
      token,
      batchIdsBefore: new Set((await listBatches(api)).map((b) => b.sample_batch_id))
    }

    // --- Upload through the Uppy dashboard on the Raw files tab ---
    // The Uppy modal overlays the page as soon as a click lands, so a normal
    // Playwright click never sees its post-click actionability pass; dispatch
    // the events directly. The upload outcome is asserted through the API
    // below (the status bar unmounts on completion, so it is not a reliable
    // signal to wait on).
    await openRawFilesTab(page)
    await page.locator('#uppy-upload-trigger').dispatchEvent('click')
    await page.locator('.uppy-Dashboard-input').first().setInputFiles(staged)
    const uploadButton = page.locator('.uppy-StatusBar-actionBtn--upload')
    await uploadButton.waitFor({ state: 'visible' })
    await uploadButton.dispatchEvent('click')

    // --- The converter ingests the upload and registers the canonical
    // sample file record (conversion ran: the stored name embeds the
    // acquisition timestamp read from inside the raw file, so search a wide
    // datetime range for our unique token instead of an exact name). Deeper
    // pipeline verification - peaks and matches reproducing golden outputs -
    // belongs to the reproducibility test, not this UI journey.
    let sampleFile
    await expect
      .poll(
        async () => {
          const res = await api.get(FILES_QUERY)
          sampleFile = (res.data ?? []).find((f) => f.filename.includes(token))
          return Boolean(sampleFile)
        },
        { timeout: 5 * 60_000, intervals: [5_000], message: 'sample file record' }
      )
      .toBe(true)
    expect(sampleFile.instrument).toBeTruthy()
    expect(sampleFile.polarity).toBeTruthy()

    // --- Browse: the file is findable in the Raw files tab ---
    // The demo measurement was acquired in the past, so widen the time range
    // (filling Min. Datetime switches the pane to range mode), then filter by
    // our unique token.
    await page.reload()
    await page.locator('#instrument-selector').waitFor({ state: 'attached' })
    await openRawFilesTab(page)
    await page.locator('#min-datetime').fill('2000/01/01 00:00')
    await page.locator('#min-datetime').press('Enter')
    await page.getByPlaceholder('Search filenames').fill(token)
    await expect(page.getByRole('cell', { name: new RegExp(token) })).toBeVisible({
      timeout: 60_000
    })

    // afterEach removes the upload again.
  })
})

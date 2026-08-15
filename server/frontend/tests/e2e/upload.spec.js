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
  for (const version of readdirSync(cacheRoot)) {
    const rawDir = path.join(cacheRoot, version, 'raw')
    if (!existsSync(rawDir)) continue
    const raw = readdirSync(rawDir).find((f) => f.toLowerCase().endsWith('.raw'))
    if (raw) return path.join(rawDir, raw)
  }
  return null
}

test.describe('sample file upload', () => {
  test('uploads a raw file and the pipeline processes it into a browsable sample', async ({
    page,
    api
  }) => {
    const source = findSourceRaw()
    test.skip(
      !source,
      'no demo raw file available; set MASCOPE_E2E_RAW_FILE or fetch the demo bundle (mascope demo fetch)'
    )
    // Real conversion + peak detection + matching takes a few minutes.
    test.setTimeout(10 * 60_000)

    // Stage a uniquely named copy: keep the instrument prefix (first token)
    // and the ionization token so upload validation passes, append an e2e
    // token so the canonical stored filename cannot collide with demo data.
    const id = Math.random().toString(36).slice(2, 8)
    const token = `e2e${id}`
    const uploadName = `${path.basename(source, '.raw')}_${token}.raw`
    const staged = path.join(mkdtempSync(path.join(tmpdir(), 'mascope-e2e-')), uploadName)
    copyFileSync(source, staged)

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
          const res = await api.get(
            '/sample/files?datetime_min=2000-01-01T00:00:00Z&limit=1000'
          )
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

    // No cleanup: the delete route only removes files without sample items,
    // and this file has been processed into one. Each run uploads under a
    // fresh unique name, so leftovers never collide; CI stacks are ephemeral.
  })
})

import { untilStoreSettled } from '@/lib/store/settle'

import { useSampleScroller } from './sampleScroller.js'

/**
 * Focus a sample, then one of its peaks, and bring the Sample tab (spectrum
 * and inspector) forward.
 *
 * The click-through the batch chart offers on a data point and the batch
 * ledger offers on a row, spelled out once. The sample switch reloads the peak
 * store, so the peak is looked up only once that reload has settled - and the
 * wait is unconditional, because the reload to outlast is not always one this
 * call started: the sample table focusing a sample a moment ago, or an earlier
 * call still waiting here, leaves the store just as stale. Waiting also gives
 * overlapping calls one wake-up point, so the last one focuses last.
 *
 * A load that never settles degrades the call to sample-focus only (see
 * `@/lib/store/settle` for the backstop), which is why a missing peak is
 * "nothing to focus" rather than an error. Ids are compared as strings: the
 * sample-peaks feed and the batch-peak series do not agree on the type.
 *
 * @param {object} app - the app facade (`useApp()`)
 * @param {object} sample - the sample record to focus
 * @param {string|number|null} samplePeakId - the peak to focus within it, or
 *   nothing to stop at the sample
 * @returns {Promise<'sample'|'peak'|'moved'|'missing'>} what was focused:
 *   the sample alone (no peak asked for), the peak, nothing more because the
 *   focus moved to another sample while the peaks loaded, or nothing more
 *   because the peak is not in the sample's loaded list
 */
export async function focusSamplePeak(app, sample, samplePeakId) {
  app.data.sample.focus(sample)
  useSampleScroller().scrollToSample(app.data.sample.focusedId)
  if (samplePeakId == null) return 'sample'

  await untilStoreSettled(() => app.data.peak.pending)

  // A call that landed while we waited has moved on to another sample; its
  // own handler owns the focus now.
  if (app.data.sample.focusedId !== sample.sample_item_id) return 'moved'

  const peak = app.data.peak.list.find((p) => String(p.peak_id) === String(samplePeakId))
  if (!peak) return 'missing'

  app.data.peak.focus(peak)
  app.ui.tab.active = 'sample'
  return 'peak'
}

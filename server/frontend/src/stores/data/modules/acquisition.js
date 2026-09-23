import { ref, reactive, computed, watch, watchEffect } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'
import { makeLogger } from '@/lib/logging'
import { runtime } from '@/lib/runtime'
import { debounce } from '@/lib/utils'

import { useInstrument } from './instrument'

// --- pagination: default page size; rows-per-page options live in the pane.
const DEFAULT_ROWS = 100

export const useAcquisition = defineStore('app.data.acquisition', () => {
  const instrument = useInstrument()

  const logger = makeLogger({
    prefix: 'data acquisition',
    icon: '🗃️'
  })

  // --- list state
  const list = ref([])
  const selected = ref([])
  const focused = computed(() => (selected.value.length === 1 ? selected.value[0] : null))
  const multiselected = computed(() => selected.value.length > 1)
  const unfocus = () => {
    selected.value = []
  }

  const ready = reactive({
    filename: null
  })

  // --- pagination state
  // DataTable lazy mode binds `first` (offset in rows) and `rows` (page size).
  // The API expects `page = first / rows` and `limit = rows`.
  const first = ref(0)
  const rows = ref(DEFAULT_ROWS)
  const total = ref(0)

  // --- sort state: bound to DataTable; @sort event triggers reload.
  // API field names match SampleFile columns; `datetime` UI column maps to
  // `datetime_utc` server-side
  const SORT_FIELD_MAP = { datetime: 'datetime_utc' }
  const sortField = ref('datetime')
  const sortOrder = ref(-1)

  // --- time filter
  const time = reactive(initTime())
  const days = computed(() =>
    time.mode == 'range' ? null : time.mode == 'Last 24 hours' ? 1 : Number(time.mode.split(' ')[1])
  )
  // Coerce mode to 'range' when an explicit range is set, and back to a
  // recent preset when both ends are cleared.
  watchEffect(() => {
    if (time.range.min || time.range.max) {
      time.mode = 'range'
    } else if (time.mode == 'range') {
      time.mode = 'Last 24 hours'
    }
  })

  // --- page filters: narrow the loaded page on the client (filename search
  // and polarity). Kept here rather than in the pane, so that opening files
  // from elsewhere can clear them.
  const search = ref('')
  const polarity = ref('')

  // --- processing status filter: the statuses to keep, or null for any.
  // Server-side, so it spans every page. With a recent preset, "recent" is
  // then when a file's status was recorded rather than when it was acquired:
  // a file uploaded or re-processed long after acquisition is still found.
  const processingStatus = ref(null)
  const keepsStatus = (record) =>
    !processingStatus.value || processingStatus.value.includes(record.processing_status)

  // Single watcher for both filters: reset paginator + selection, then
  // reload, once however many of them changed in the same tick (Clear
  // filters changes both). Ordering matters - the reload must see first=0 to
  // fetch page 0.
  watch([time, processingStatus], async () => {
    unfocus()
    first.value = 0
    await load()
  })

  // --- instrument: reset to page 0 + reload on change; manage socket rooms.
  watch(
    computed(() => instrument.focused?.instrument),
    async () => {
      first.value = 0
      await load()
    }
  )
  watch(
    () => instrument.focused,
    () => unfocus()
  )

  // --- socket rooms. An acquisition event is emitted into its instrument's
  // own room, so listing every instrument means holding every one of those
  // rooms - one focused instrument is not a special case of that, it is one
  // room instead of all of them. Reconciled rather than toggled, because the
  // instrument list loads after the store and grows as files arrive.
  let subscribed = new Set()
  const rooms = computed(() =>
    instrument.focused
      ? [instrument.focused.instrument]
      : (instrument.list ?? []).map((known) => known.instrument)
  )
  watch(
    () => rooms.value.join(','),
    () => {
      const wanted = new Set(rooms.value)
      for (const room of subscribed) {
        if (!wanted.has(room)) api.socket.removeSubscription(room)
      }
      for (const room of wanted) {
        if (!subscribed.has(room)) api.socket.addSubscription(room)
      }
      subscribed = wanted
    },
    { immediate: true }
  )

  /** Whether the list is showing an instrument's files at all. */
  const listsInstrument = (name) => !instrument.focused || instrument.focused.instrument === name

  // --- loading: only the latest request's answer is kept, and a row update
  // that arrives while a load is in flight is applied again on top of its
  // answer, which may predate it.
  let latestLoad = 0
  let updatesDuringLoad = null

  async function load() {
    const loadId = ++latestLoad
    updatesDuringLoad ??= new Map()
    let answer = null
    if (time.mode.startsWith('Last')) {
      answer = await loadRecent(days.value)
    } else if (time.mode == 'range') {
      answer = await loadRange(time.range)
    }
    if (loadId !== latestLoad) return
    const updates = updatesDuringLoad
    updatesDuringLoad = null
    if (!answer) return
    list.value = answer.items
    total.value = answer.results
    for (const [recordId, record] of updates) applyUpdate(recordId, record)
  }

  // Raw axios call (no `use: read` handler) so we can read both `data` and
  // `results` from the unified response envelope for the paginator total.
  async function loadRecent(daysCount = 7) {
    try {
      const response = await api.http.get('/sample/files/recent', {
        params: {
          instrument: instrument.focused?.instrument,
          sort: SORT_FIELD_MAP[sortField.value] ?? sortField.value,
          order: sortOrder.value === 1 ? 'asc' : 'desc',
          days: daysCount,
          recent_by: processingStatus.value ? 'processing' : undefined,
          processing_status: processingStatus.value ?? undefined,
          page: Math.floor(first.value / rows.value),
          limit: rows.value
        },
        // Repeat the key for each status (`a=1&a=2`), the form the API reads
        // a list from.
        paramsSerializer: { indexes: null },
        type: 'load_recent_sample_files'
      })
      const { data: items = [], results = 0 } = response.data ?? {}
      return { items, results }
    } catch (err) {
      logger.error(`failed to load recent sample files: ${err}`)
      return null
    }
  }

  async function loadRange(range) {
    try {
      const response = await api.http.get('/sample/files', {
        params: {
          datetime_min: range.min?.toISOString(),
          datetime_max: range.max?.toISOString(),
          instrument: instrument.focused?.instrument,
          sort: SORT_FIELD_MAP[sortField.value] ?? sortField.value,
          order: sortOrder.value === 1 ? 'asc' : 'desc',
          processing_status: processingStatus.value ?? undefined,
          page: Math.floor(first.value / rows.value),
          limit: rows.value
        },
        paramsSerializer: { indexes: null },
        type: 'load_sample_file_range'
      })
      const { data: items = [], results = 0 } = response.data ?? {}
      return { items, results }
    } catch (err) {
      logger.error(`failed to load sample file range: ${err}`)
      return null
    }
  }

  // --- paginator handler wired to DataTable's @page event, clears selection.
  // Skip reload when neither offset nor page size changed (guards against
  // PrimeVue's spurious @page emission on mount with lazy + :first bound).

  function setPage(event) {
    if (event.first === first.value && event.rows === rows.value) return
    first.value = event.first
    rows.value = event.rows
    unfocus()
    load()
  }

  // --- sort handler wired to DataTable's @sort event, clears selection and resets paginator.
  function setSort(event) {
    sortField.value = event.sortField ?? 'datetime'
    sortOrder.value = event.sortOrder ?? -1
    first.value = 0
    unfocus()
    load()
  }

  // --- socket events: refetch current page on create/delete to keep page
  // contents and total count consistent; update in place on update.
  api.socket.on('acquisition_created', (payload) => {
    const { record } = payload
    if (listsInstrument(record.instrument)) {
      load()
    }
  })

  // A status change can move a file into or out of a status filter, which
  // only a reload can place on the right page. Debounced: every file writes
  // several statuses in quick succession.
  const reloadForStatus = debounce(() => load(), 300)

  function applyUpdate(recordId, record) {
    const index = list.value.findIndex((f) => f.sample_file_id === recordId)
    if (index >= 0) {
      if (keepsStatus(record)) {
        list.value[index] = record
        logger.log(`updated ${record.filename}`)
      } else {
        reloadForStatus()
      }
    } else if (
      processingStatus.value &&
      keepsStatus(record) &&
      listsInstrument(record.instrument)
    ) {
      reloadForStatus()
    }
  }

  api.socket.on('acquisition_updated', (payload) => {
    const { record_id, record } = payload
    updatesDuringLoad?.set(record_id, record)
    applyUpdate(record_id, record)
  })

  api.socket.on('acquisition_deleted', (payload) => {
    const { record_id } = payload
    if (selected.value.some((s) => s.sample_file_id === record_id)) {
      unfocus()
    }
    load()
  })

  const resetFilters = () => {
    selected.value = []
    first.value = 0
    time.mode = initTime().mode
    time.range = initTime().range
    processingStatus.value = null
    search.value = ''
    polarity.value = ''
  }

  /**
   * Show the files of an instrument that ended in one status, acquired since
   * a time: what a kept notification names. The page filters are cleared, so
   * none of them hides what was asked for.
   *
   * @param {object} files
   * @param {string} files.instrument The instrument.
   * @param {string|null} files.status The status, or null for any.
   * @param {Date|null} files.since The earliest acquisition time, if any.
   */
  function showFiles({ instrument: name, status, since }) {
    instrument.focus({ instrument: name })
    search.value = ''
    polarity.value = ''
    processingStatus.value = status ? [status] : null
    if (since) {
      time.range.min = since
      time.range.max = null
    }
  }

  return {
    // state
    list,
    selected,
    focused,
    multiselected,
    unfocus,
    ready,
    time,
    processingStatus,
    search,
    polarity,
    first,
    rows,
    total,
    // actions
    load,
    setPage,
    sortField,
    sortOrder,
    setSort,
    resetFilters,
    showFiles
  }
})

const toDate = (iso) => (iso ? new Date(iso) : null)

function initTime() {
  const configured = runtime.config.acquisition_filter
  if (configured) {
    if (typeof configured == 'string') {
      return { mode: configured, range: { min: null, max: null } }
    } else if (typeof configured == 'object') {
      return {
        mode: 'range',
        range: { min: toDate(configured.min), max: toDate(configured.max) }
      }
    }
  }
  return { mode: 'Last 24 hours', range: { min: null, max: null } }
}

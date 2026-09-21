import { ref, reactive, computed, watch, watchEffect } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'
import { makeLogger } from '@/lib/logging'
import { runtime } from '@/lib/runtime'

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
  // Single watcher: reset paginator + selection, then reload. Ordering
  // matters - the reload must see first=0 to fetch page 0.
  watch(time, async () => {
    unfocus()
    first.value = 0
    await load()
  })

  // --- processing status filter: the statuses to keep, or null for any.
  // Server-side, so it spans every page; a change reloads from page 0.
  const processingStatus = ref(null)
  watch(processingStatus, async () => {
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
    (next, prev) => {
      unfocus()
      if (prev) api.socket.removeSubscription(prev.instrument)
      if (next) api.socket.addSubscription(next.instrument)
    }
  )

  async function load() {
    if (time.mode.startsWith('Last')) {
      await loadRecent(days.value)
    } else if (time.mode == 'range') {
      await loadRange(time.range)
    }
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
      list.value = items
      total.value = results
    } catch (err) {
      logger.error(`failed to load recent sample files: ${err}`)
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
      list.value = items
      total.value = results
    } catch (err) {
      logger.error(`failed to load sample file range: ${err}`)
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
    if (record.instrument === instrument.focused?.instrument) {
      load()
    }
  })

  api.socket.on('acquisition_updated', (payload) => {
    const { record_id, record } = payload
    const index = list.value.findIndex((f) => f.sample_file_id === record_id)
    if (index >= 0) {
      list.value[index] = record
      logger.log(`updated ${record.filename}`)
    }
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
    first,
    rows,
    total,
    // actions
    load,
    setPage,
    sortField,
    sortOrder,
    setSort,
    resetFilters
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

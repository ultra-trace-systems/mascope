import { describe, it, expect, vi, beforeEach } from 'vitest'
import { nextTick, effectScope, defineComponent, h } from 'vue'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// The store registers a socket listener at setup; a stub is all it needs.
vi.mock('@/api', () => ({
  api: {
    socket: { on: vi.fn(), id: 'test-sid' },
    http: { get: vi.fn(), post: vi.fn() }
  }
}))

import { useNotification } from '@/stores/ui/notification'

describe('notification store', () => {
  let store

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
    store = useNotification()
  })

  it('displays and logs a plain notification', () => {
    store.push({ type: 'info', status: 'success', message: 'saved' })

    expect(store.latest.message).toBe('saved')
    expect(store.log).toHaveLength(1)
  })

  it('counts warnings and errors until the badge is cleared', () => {
    store.push({ type: 'x', status: 'warning', message: 'w' })
    store.push({ type: 'x', status: 'error', message: 'e' })

    expect(store.recentWarnings).toBe(1)
    expect(store.recentErrors).toBe(1)

    store.clearRecentBadge()

    expect(store.recentWarnings).toBe(0)
    expect(store.recentErrors).toBe(0)
  })

  it('tracks a pending process without logging it', () => {
    store.push({
      type: 'mz_fit',
      status: 'pending',
      process_id: 'p1',
      message: 'working',
      progress: 10
    })

    expect(store.progress).toHaveLength(1)
    expect(store.log).toHaveLength(0)
  })

  it('completes a process: removed from progress, logged and displayed', () => {
    store.push({ type: 'mz_fit', status: 'pending', process_id: 'p1', progress: 10 })
    store.push({ type: 'mz_fit', status: 'success', process_id: 'p1', message: 'done' })

    expect(store.progress).toHaveLength(0)
    expect(store.log).toHaveLength(1)
    expect(store.latest.message).toBe('done')
  })

  it('logs but does not display child process notifications', () => {
    store.push({
      type: 'mz_fit',
      status: 'success',
      process_id: 'child',
      parent_id: 'root',
      message: 'child done'
    })

    expect(store.log).toHaveLength(1)
    expect(store.latest).toBe(null)
  })

  it('ends a tracked process on a silent packet, without logging or counting it', () => {
    store.push({
      type: 'calibration_mz_fit',
      status: 'pending',
      process_id: 'child',
      parent_id: 'root',
      progress: 10
    })
    expect(store.progress).toHaveLength(1)

    // What the backend sends when a parent handler reports the warning: the
    // bar has to clear now, not 30 seconds later, but nothing about it may
    // reach the drawer or the badge.
    store.push({
      type: 'calibration_mz_fit',
      status: 'warning',
      process_id: 'child',
      parent_id: 'root',
      message: 'careful',
      silent: true
    })

    expect(store.progress).toHaveLength(0)
    expect(store.log).toHaveLength(0)
    expect(store.recentWarnings).toBe(0)
    expect(store.latest).toBe(null)
  })

  it('ignores a silent packet for a process it is not tracking', () => {
    store.push({ type: 'mz_fit', status: 'warning', process_id: 'gone', silent: true })

    expect(store.progress).toHaveLength(0)
    expect(store.log).toHaveLength(0)
    expect(store.recentWarnings).toBe(0)
    expect(store.latest).toBe(null)
  })

  it('expires an idle pending process after its timeout', () => {
    store.push({ type: 'mz_fit', status: 'pending', process_id: 'p1', progress: 10 })
    expect(store.progress).toHaveLength(1)

    vi.advanceTimersByTime(31 * 1000)

    expect(store.progress).toHaveLength(0)
  })

  it('triggers watchers for matching types and the wildcard', async () => {
    const onMzFit = vi.fn()
    const onAnything = vi.fn()
    store.on('mz_fit', onMzFit)
    store.on('*', onAnything)

    store.push({ type: 'mz_fit', status: 'success', message: 'done' })
    await nextTick()

    expect(onMzFit).toHaveBeenCalledOnce()
    expect(onAnything).toHaveBeenCalledOnce()

    store.push({ type: 'other', status: 'success', message: 'x' })
    await nextTick()

    expect(onMzFit).toHaveBeenCalledOnce()
    expect(onAnything).toHaveBeenCalledTimes(2)
  })

  it('caps the log at the retention limit', () => {
    for (let i = 0; i < 260; i++) {
      store.push({ type: 'x', status: 'success', message: `m${i}` })
    }

    expect(store.log).toHaveLength(250)
    expect(store.log[0].message).toBe('m259')
  })

  it('clears the log without cancelling live processes', () => {
    store.push({ type: 'mz_fit', status: 'pending', process_id: 'p1', progress: 10 })
    store.push({ type: 'x', status: 'success', message: 'saved' })
    expect(store.log).toHaveLength(1)

    store.clearLog()

    expect(store.log).toHaveLength(0)
    expect(store.progress).toHaveLength(1)
  })

  it('keeps logging after a clear', () => {
    store.push({ type: 'x', status: 'success', message: 'a' })
    store.clearLog()
    store.push({ type: 'x', status: 'success', message: 'b' })

    expect(store.log).toHaveLength(1)
    expect(store.log[0].message).toBe('b')
  })

  it('clears the unread badge along with the log', () => {
    store.push({ type: 'x', status: 'warning', message: 'w' })
    store.push({ type: 'x', status: 'error', message: 'e' })
    expect(store.recentWarnings).toBe(1)
    expect(store.recentErrors).toBe(1)

    store.clearLog()

    // The badge sits on the bell right above the feed the user just emptied,
    // so it must not keep a count for rows that are gone.
    expect(store.recentWarnings).toBe(0)
    expect(store.recentErrors).toBe(0)
  })

  it('keeps result payloads out of the log', () => {
    store.push({
      type: 'match_compositions_by_mz',
      status: 'success',
      message: 'matched',
      data: { results: [1, 2, 3] },
      error: { detail: 'x' }
    })

    expect(store.log[0]).not.toHaveProperty('data.results')
    expect(store.log[0].data).toBeUndefined()
    expect(store.log[0].error).toBeUndefined()
  })

  it('keeps the full payload on the displayed notification', () => {
    const data = { fit: { slope: 1 }, download: 'report.xlsx' }
    const error = { detail: { data: { download: 'report.xlsx' } } }
    store.push({ type: 'calibration_mz_fit', status: 'success', message: 'ok', data, error })

    expect(store.latest.data).toEqual(data)
    expect(store.latest.error).toEqual(error)
  })

  it('logs the fields the notification drawer renders', () => {
    store.push({
      type: 'mz_fit',
      status: 'warning',
      process_id: 'p1',
      message: 'careful',
      data: { big: 'payload' }
    })

    const entry = store.log[0]
    expect(entry.type).toBe('mz_fit')
    expect(entry.status).toBe('warning')
    expect(entry.message).toBe('careful')
    expect(entry.process_id).toBe('p1')
    // NotificationPane calls timestamp.toISOString().
    expect(entry.timestamp).toBeInstanceOf(Date)
  })

  it('tolerates a notification with no message or payload', () => {
    store.push({ type: 'x', status: 'success' })

    expect(store.log).toHaveLength(1)
    expect(store.log[0].message).toBeUndefined()
  })

  it('drops a watcher when the scope that registered it goes away', async () => {
    const cb = vi.fn()
    const scope = effectScope()
    scope.run(() => store.on('probe', cb))

    store.push({ type: 'probe', status: 'success', message: 'a' })
    await nextTick()
    expect(cb).toHaveBeenCalledOnce()

    scope.stop()
    store.push({ type: 'probe', status: 'success', message: 'b' })
    await nextTick()

    expect(cb).toHaveBeenCalledOnce()
  })

  it('does not accumulate watchers across mount cycles', async () => {
    const cb = vi.fn()
    const Pane = defineComponent({
      setup() {
        store.on('probe', cb)
        return () => h('div')
      }
    })

    for (let i = 0; i < 5; i++) {
      mount(Pane).unmount()
    }
    store.push({ type: 'probe', status: 'success', message: 'a' })
    await nextTick()

    expect(cb).not.toHaveBeenCalled()
  })

  it('keeps a watcher registered while its component is mounted', async () => {
    const cb = vi.fn()
    const Pane = defineComponent({
      setup() {
        store.on('probe', cb)
        return () => h('div')
      }
    })

    const wrapper = mount(Pane)
    store.push({ type: 'probe', status: 'success', message: 'a' })
    await nextTick()

    expect(cb).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('removes a watcher registered outside any scope on request', async () => {
    const cb = vi.fn()
    const handler = store.on('probe', cb)

    store.push({ type: 'probe', status: 'success', message: 'a' })
    await nextTick()
    expect(cb).toHaveBeenCalledOnce()

    handler.remove()
    store.push({ type: 'probe', status: 'success', message: 'b' })
    await nextTick()

    expect(cb).toHaveBeenCalledOnce()
  })
})

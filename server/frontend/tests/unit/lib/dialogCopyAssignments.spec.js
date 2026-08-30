import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

// The copy launcher's job is to show the batch as the SERVER sees it - which of
// the other samples a copy would land on, and why the rest would be skipped -
// and to refuse to launch when there is nothing to copy or nowhere to copy it.
// Its eligibility list is not assembled client-side (no loaded record carries a
// per-sample run status), so these tests pin that it renders the preview it was
// given and gates the button on it.

const copyPreview = vi.fn()
const copyToBatch = vi.fn()
const push = vi.fn()

// Minimal help-mode facade: the dialog registers a help card through these; the
// tests only need them to resolve.
const helpStub = {
  set: vi.fn(),
  docUrl: (path = '') => `/docs/${path}`,
  directive: () => ({}),
  right: () => ({}),
  left: () => ({}),
  top: () => ({}),
  bottom: () => ({}),
  bottom_end: () => ({})
}

vi.mock('@/stores', () => ({
  useApp: () => ({
    data: { peakAssignmentRun: { copyPreview, copyToBatch } },
    ui: { notification: { push }, help: helpStub }
  })
}))

const { default: DialogCopyAssignments } = await import('@/lib/dialogs/DialogCopyAssignments.vue')

const SAMPLE = { sample_item_id: 'si-source', sample_item_name: 'Curated Sample' }

/** The preview payload the backend serves, as the store resolves it. */
function preview({ runId = 'run-1', destinations = [] } = {}) {
  return [
    {
      sample_item_id: 'si-source',
      sample_batch_id: 'sb-1',
      source_peak_assignment_run_id: runId,
      source_engine: 'mascope',
      destinations
    }
  ]
}

const ELIGIBLE = {
  sample_item_id: 'si-1',
  sample_item_name: 'Sibling One',
  eligible: true,
  reason: null
}
const BLANK = {
  sample_item_id: 'si-2',
  sample_item_name: 'A Blank',
  eligible: false,
  reason: 'blank sample (no peaks)'
}
const WRONG_POLARITY = {
  sample_item_id: 'si-3',
  sample_item_name: 'Negative Mode',
  eligible: false,
  reason: "different polarity ('-' vs source '+')"
}

/** Whether the launcher asked its parent to close the dialog. */
function closedBy(wrapper) {
  const events = wrapper.emitted('update:visible') ?? []
  return events.length > 0 && events[events.length - 1][0] === false
}

/** An axios-shaped rejection carrying a server message. */
function apiError(status, message) {
  return { response: { status, data: { error: message } } }
}

const GLOBAL_STUBS = {
  Dialog: { template: '<div><slot /><slot name="footer" /></div>' },
  Message: { template: '<div><slot /></div>' },
  Button: {
    props: { label: { type: String, default: '' }, disabled: { type: Boolean, default: false } },
    template: '<button :disabled="disabled">{{ label }}<slot /></button>'
  },
  ProgressSpinner: true,
  // Typed so the rendered eligibility rows can be read back by value.
  Tag: {
    props: { value: { type: String, default: '' }, severity: { type: String, default: null } },
    template: '<span class="tag" :data-severity="severity">{{ value }}</span>'
  }
}

// Mounted closed and then opened, which is how the dialog actually lives: it
// is mounted once in SampleTable and its visibility is toggled, so opening -
// not mounting - is what triggers the preview read.
async function openDialog(sample = SAMPLE) {
  const wrapper = mount(DialogCopyAssignments, {
    props: { visible: false, sample },
    global: { stubs: GLOBAL_STUBS }
  })
  await wrapper.setProps({ visible: true })
  // Let the watcher's fetch and its promise settle.
  await flushPromises()
  return wrapper
}

describe('DialogCopyAssignments', () => {
  beforeEach(() => {
    copyPreview.mockReset()
    copyPreview.mockResolvedValue(preview({ destinations: [ELIGIBLE] }))
    copyToBatch.mockReset()
    copyToBatch.mockResolvedValue(undefined)
    push.mockReset()
  })
  afterEach(() => vi.clearAllMocks())

  it('asks the backend what a copy would do when it opens', async () => {
    await openDialog()

    expect(copyPreview).toHaveBeenCalledWith('si-source')
  })

  it('lists every sibling with the verdict the server gave it', async () => {
    copyPreview.mockResolvedValue(preview({ destinations: [ELIGIBLE, BLANK, WRONG_POLARITY] }))
    const wrapper = await openDialog()

    const text = wrapper.text()
    expect(text).toContain('Sibling One')
    expect(text).toContain('A Blank')
    expect(text).toContain('Negative Mode')
    // The reasons are the server's own words, not a client-side guess.
    expect(text).toContain('blank sample (no peaks)')
    expect(text).toContain('different polarity')
  })

  it('counts only the eligible destinations on the launch button', async () => {
    copyPreview.mockResolvedValue(preview({ destinations: [ELIGIBLE, BLANK, WRONG_POLARITY] }))
    const wrapper = await openDialog()

    expect(wrapper.vm.eligible).toHaveLength(1)
    expect(wrapper.text()).toContain('Copy to 1 sample')
  })

  it('launches the copy for the source sample', async () => {
    const wrapper = await openDialog()

    await wrapper.vm.launch()

    expect(copyToBatch).toHaveBeenCalledWith('si-source')
    expect(closedBy(wrapper)).toBe(true)
    expect(push).not.toHaveBeenCalled()
  })

  it('will not offer a copy when the source has no completed run', async () => {
    copyPreview.mockResolvedValue(preview({ runId: null, destinations: [ELIGIBLE] }))
    const wrapper = await openDialog()

    expect(wrapper.vm.canCopy).toBe(false)
    expect(wrapper.text()).toContain('no completed assignment run')
  })

  it('will not offer a copy when nothing in the batch is eligible', async () => {
    copyPreview.mockResolvedValue(preview({ destinations: [BLANK, WRONG_POLARITY] }))
    const wrapper = await openDialog()

    expect(wrapper.vm.canCopy).toBe(false)
    expect(wrapper.text()).toContain('None of the batch')
  })

  it('says so when the batch holds no other samples', async () => {
    copyPreview.mockResolvedValue(preview({ destinations: [] }))
    const wrapper = await openDialog()

    expect(wrapper.vm.canCopy).toBe(false)
    expect(wrapper.text()).toContain('no other samples')
  })

  it('reports a preview that could not be read, rather than an empty list', async () => {
    copyPreview.mockRejectedValue(apiError(500, 'Unexpected error (ref: abc12345).'))
    const wrapper = await openDialog()

    expect(wrapper.text()).toContain('Unexpected error')
    expect(wrapper.vm.canCopy).toBe(false)
  })

  it('closes and reports the reason when the launch is refused', async () => {
    copyToBatch.mockRejectedValue(
      apiError(422, "Sample 'Curated Sample' has no completed peak assignment run to copy.")
    )
    const wrapper = await openDialog()

    await wrapper.vm.launch()

    expect(closedBy(wrapper)).toBe(true)
    expect(push).toHaveBeenCalledTimes(1)
    const notification = push.mock.calls[0][0]
    expect(notification.status).toBe('warning')
    expect(notification.message).toContain('no completed peak assignment run')
  })

  it('reports a genuine failure as an error, not a refusal', async () => {
    copyToBatch.mockRejectedValue(apiError(500, 'Unexpected error (ref: abc12345).'))
    const wrapper = await openDialog()

    await wrapper.vm.launch()

    expect(push.mock.calls[0][0].status).toBe('error')
    // The rejection must not strand the spinner.
    expect(wrapper.vm.submitting).toBe(false)
  })

  it('re-reads the batch on every open, so the list cannot go stale', async () => {
    const wrapper = await openDialog()
    expect(copyPreview).toHaveBeenCalledTimes(1)

    await wrapper.setProps({ visible: false })
    await wrapper.setProps({ visible: true })
    await flushPromises()

    expect(copyPreview).toHaveBeenCalledTimes(2)
  })

  it('does nothing without a sample', async () => {
    const wrapper = await openDialog(null)

    await wrapper.vm.launch()

    expect(copyToBatch).not.toHaveBeenCalled()
    expect(copyPreview).not.toHaveBeenCalled()
  })
})

import { ref, computed, onScopeDispose } from 'vue'
import { defineStore } from 'pinia'

import { copyText } from '@/lib/clipboard'

export const useClipboard = defineStore('browser.sample.clipboard', () => {
  const raw = ref()
  const parsed = computed(() => {
    if (raw.value) {
      try {
        return JSON.parse(raw.value)
      } catch {
        return null
      }
    }
    return null
  })
  const data = computed(() => parsed.value?.data)
  const op = computed(() => parsed.value?.op)
  const dataset = computed(() => (isDataset(data.value) ? data.value : null))
  const batch = computed(() => {
    if (isBatch(data.value)) {
      return data.value
    } else {
      return null
    }
  })
  const samples = computed(() => {
    if (Array.isArray(data.value) && data.value?.every(isSample)) {
      return data.value
    } else {
      return null
    }
  })

  // The payload goes onto the system clipboard, so another tab can paste it, and
  // is also kept here. Reading the system clipboard back needs the async
  // Clipboard API, which a page served over plain HTTP does not have and a
  // browser may refuse; the kept copy is then what this tab pastes.
  async function read() {
    if (!navigator.clipboard?.readText) return
    try {
      raw.value = await navigator.clipboard.readText()
    } catch {
      return
    }
  }

  // Other tabs of the same deployment keep their own copy too, and one that
  // cannot read the clipboard back would go on offering a cut that was pasted
  // here, with the items already moved. A pasted cut is announced on this
  // channel, and a tab holding the same payload drops it. BroadcastChannel works
  // over plain HTTP; the channel is opened only once this tab keeps or pastes a
  // payload.
  let channel = null
  function tabChannel() {
    if (!channel && typeof BroadcastChannel !== 'undefined') {
      channel = new BroadcastChannel('mascope.browser.sample.clipboard')
      channel.onmessage = ({ data }) => {
        if (data?.pasted && data.pasted === raw.value) raw.value = null
      }
    }
    return channel
  }
  onScopeDispose(() => channel?.close())

  async function write({ op, data }) {
    if (!op || !['copy', 'cut'].includes(op)) {
      throw Error("clipboard writing must include an 'op' field with value 'copy' or 'cut'")
    }
    const text = JSON.stringify({ op, data })
    raw.value = text
    tabChannel()
    if (!(await copyText(text))) {
      console.warn('Could not put the copied items on the system clipboard')
    }
  }
  async function copy(data) {
    await write({ op: 'copy', data })
  }
  async function cut(data) {
    await write({ op: 'cut', data })
  }

  // After a cut is pasted the items have moved, so the payload must not be
  // offered again. Only the async API can blank the system clipboard here: this
  // runs once the move request returns, too late for the copy command, which a
  // browser allows only from the click itself. Without the API nothing on this
  // page can read the payload back, so dropping the kept copies - here and in
  // the other tabs - is enough.
  async function clear() {
    const pasted = raw.value
    raw.value = null
    if (pasted) tabChannel()?.postMessage({ pasted })
    if (!navigator.clipboard?.writeText) return
    try {
      await navigator.clipboard.writeText('')
    } catch {
      return
    }
  }

  return {
    op,
    dataset,
    batch,
    samples,
    copy,
    cut,
    read,
    clear
  }
})

function isDataset(record) {
  return (
    record?.dataset_id &&
    record?.dataset_name &&
    !record?.sample_batch_id &&
    !record?.sample_item_id
  )
}

function isBatch(record) {
  return record?.sample_batch_id && !record?.sample_item_id
}

function isSample(record) {
  return record.sample_item_id && record.sample_batch_id && record.sample_item_name
}

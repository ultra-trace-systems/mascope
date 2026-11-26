import { describe, it, expect } from 'vitest'
import { nextTick, ref } from 'vue'

import { useFamilyFold } from '@/lib/ledger/useFamilyFold.js'

describe('useFamilyFold', () => {
  it('toggles one family against Show, and starts over when Show moves', async () => {
    const all = ref(false)
    const families = useFamilyFold(all)
    families.toggle('a')
    expect(families.isOpen('a')).toBe(true)
    expect(families.isOpen('b')).toBe(false)
    all.value = true
    await nextTick()
    expect(families.isOpen('a')).toBe(true)
    expect(families.isOpen('b')).toBe(true)
  })

  // What a hand assignment opens: the family it wrote into, and a family the
  // reader had open stays open, where a toggle would shut it.
  it('opens a family and leaves an open one open', () => {
    const all = ref(false)
    const families = useFamilyFold(all)
    families.open('a')
    expect(families.isOpen('a')).toBe(true)
    families.open('a')
    expect(families.isOpen('a')).toBe(true)

    all.value = true
    const shown = useFamilyFold(all)
    shown.open('b')
    expect(shown.isOpen('b')).toBe(true)
  })
})

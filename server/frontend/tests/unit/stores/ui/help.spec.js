import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { docUrl, DOCS_BASE, useHelp } from '@/stores/ui/help'

describe('help docUrl', () => {
  it('builds a link under the docs base', () => {
    expect(docUrl('how-it-works/matching/')).toBe('/docs/how-it-works/matching/')
  })

  it('defaults to the docs home', () => {
    expect(docUrl()).toBe('/docs/')
    expect(DOCS_BASE).toBe('/docs/')
  })

  it('strips leading slashes so the path is always relative to the base', () => {
    expect(docUrl('/concepts/')).toBe('/docs/concepts/')
    expect(docUrl('///reference/')).toBe('/docs/reference/')
  })
})

describe('help resolveMessage', () => {
  let store

  beforeEach(async () => {
    setActivePinia(createPinia())
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ matching: '<p>matching body</p>' })
    })
    store = useHelp()
    await store.loadContent()
  })

  it('renders a docs-sourced card as title + snippet body', () => {
    expect(store.resolveMessage({ helpKey: 'matching', title: 'Match view' })).toBe(
      '<h1>Match view</h1><p>matching body</p>'
    )
  })

  it('prefers an inline message when present (legacy cards)', () => {
    expect(store.resolveMessage({ message: '<h1>Inline</h1>' })).toBe('<h1>Inline</h1>')
  })

  it('falls back to the title alone when the snippet is missing (e.g. dev)', () => {
    expect(store.resolveMessage({ helpKey: 'absent', title: 'Match view' })).toBe(
      '<h1>Match view</h1>'
    )
  })

  it('is empty for no card', () => {
    expect(store.resolveMessage(null)).toBe('')
  })
})

describe('help directive', () => {
  let store

  beforeEach(() => {
    setActivePinia(createPinia())
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
    store = useHelp()
  })

  const mount = (layer, value, modifiers) => {
    const element = document.createElement('div')
    document.body.appendChild(element)
    store.directive(layer).mounted(element, { value, modifiers })
    return store.cards.at(-1)
  }

  it('passes docs-sourced card options through from plain elements', () => {
    const card = mount(
      'dialog_peak_assign',
      { title: 'Confidence Tiers', helpKey: 'assignment-tiers', doc: '/docs/x/' },
      { right: true }
    )
    expect(card).toMatchObject({
      title: 'Confidence Tiers',
      helpKey: 'assignment-tiers',
      doc: '/docs/x/',
      placement: 'right',
      layer: 'dialog_peak_assign'
    })
  })

  it('still registers a bare string as an inline message on the default layer', () => {
    const card = mount(undefined, '<h1>Inline</h1>', { top: true })
    expect(card).toMatchObject({ message: '<h1>Inline</h1>', placement: 'top', layer: 'default' })
  })

  it('does not let a card override its directive layer', () => {
    const card = mount('sidebar_x', { message: 'm', layer: 'other' }, { bottom: true })
    expect(card.layer).toBe('sidebar_x')
  })
})

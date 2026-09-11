// Copy text to the clipboard, including where the async Clipboard API does not
// exist. `navigator.clipboard` is only there in a secure context - HTTPS, or
// localhost - so a deployment served over plain HTTP on a LAN address has none,
// and code that calls it unguarded throws. The legacy copy command still works
// there, from a user gesture, on a selected text field.

/**
 * Put `text` on the clipboard.
 *
 * @param {string} text
 * @param {HTMLElement} [container] - where to put the temporary text field.
 *   Pass an element inside an open drawer or dialog: a focus trap would pull
 *   focus back out of a field appended to <body>, and the copy would miss.
 * @returns {Promise<boolean>} whether either path copied
 */
export const copyText = async (text, container = document.body) => {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // Refused (permissions, an unfocused document): try the legacy path.
    }
  }
  const field = document.createElement('textarea')
  field.value = text
  field.setAttribute('readonly', '')
  // Out of view and out of layout, but still selectable.
  Object.assign(field.style, { position: 'fixed', top: '0', left: '0', opacity: '0' })
  container.appendChild(field)
  try {
    field.select()
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    field.remove()
  }
}

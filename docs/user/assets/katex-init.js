/* Render arithmatex math with the self-hosted KaTeX bundle (assets/katex/).
   document$ re-fires on every page change under navigation.instant, so math
   renders after in-app navigation too, not just on full page loads. */
document$.subscribe(({ body }) => {
  renderMathInElement(body, {
    delimiters: [
      { left: '$$', right: '$$', display: true },
      { left: '$', right: '$', display: false },
      { left: '\\(', right: '\\)', display: false },
      { left: '\\[', right: '\\]', display: true }
    ]
  })
})

- **The match tree no longer counts an isotope line off its ratio by 100 % or
  more in its ion's intensity.** Such a line, whose peak reads at twice its
  expected share or more, or a minor line of an ion whose most abundant line
  has no peak, now loses its intensity as well as its score. The batch chart
  never counted it, since a batch match stores no line that scored 0, so the
  live and the stored intensities agree again. A smaller ratio miss still
  keeps the line's intensity (#3000).

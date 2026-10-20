- **The Match tab's time series chart no longer disappears.** The chart
  held its height in its Plotly layout, and Plotly's responsive resize
  deletes such a height and fits the chart to its container instead, which
  had no height of its own: the chart shrank to nothing until a splitter was
  moved. Plotly 4.1.2, which 1.12.0 ships, runs that resize whenever the
  chart's own box changes size, not only when the browser window does, so
  a slightly narrower panel was enough. The chart now takes its height from
  its container, as the other charts do.

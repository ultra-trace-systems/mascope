- **`mascope_viz` draws charts: one Figure spec, two renderers.** A chart is a
  `mascope_viz.charts.Figure`: a title, a subtitle, panels over eight
  primitives (`series`, `strip`, `heatmap`, `scatter`, `sticks`, `bars`,
  `box`, `table`), the numbers a caption may cite, and the frame behind it.
  Its context carries what the view behind it says (the cadence, the
  window, the time zone, the normalisation, a sum of reagent ions
  included, and the run changes) as data, and the subtitle is written from
  it. A series is shown by its display name, never by its key.
  `mascope_viz.render.matplotlib` draws it for PNG, SVG and PDF, and
  `mascope_viz.render.plotly` for HTML. `render.save(fig, path)` picks the
  renderer by the file's extension, in a light or a dark theme. Both draw a
  gap as a gap, never a line across it, and wrap a long subtitle whole. A
  spec is also a versioned JSON document (`Figure.to_json`,
  `Figure.from_json`) that names colours by theme token, so another
  renderer can draw it later. Names are drawn as written, never as markup
  or mathtext. A PNG is the same bytes on every run, and so are an SVG and
  a PDF once `SOURCE_DATE_EPOCH` is set. The palettes clear 3:1 contrast on
  both themes' grounds. `mascope_viz.data` and `mascope_viz.charts` import
  neither matplotlib nor plotly. `mascope_viz.charts.registry` is where
  the chart kinds will register.

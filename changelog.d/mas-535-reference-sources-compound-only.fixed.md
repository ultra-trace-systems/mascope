- **The reference lists leave out spectral libraries.** `GET
  /api/reference/sources`, the **Reference lists** settings page and the SDK's
  `mascope.reference.sources()` listed an MS/MS library loaded with
  `mascope reference` beside the compound lists, counting its spectra as
  compounds, and its compounds read as an empty list. They now list compound
  sources only, and the compounds of a spectral library answer 404.

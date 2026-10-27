- **The API lists the loaded reference sources.** `GET /api/reference/sources`
  returns every active reference source with its label, version, licence,
  polarity, radical allowance, matching window, compound count, load date and
  whether the deployment's `[backend] reference_licenses` gate lets peak
  assignment match it. `GET /api/reference/sources/{name}/compounds` pages
  through the compounds of a source's active version, or of a named one, with
  each compound's formula, mass, InChIKey, the DOI its list cites and its tags.
  Any signed-in user can read both, and an API token too.

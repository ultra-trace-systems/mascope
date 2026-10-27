- **The SDK reads the server's reference lists.** `mascope.reference.sources()`
  lists the reference sources the server has loaded, one row each, with
  `references` citing the publication each shipped list comes from (its DOI
  or ISBN), and `admitted` saying whether the licence gate lets peak
  assignment match it. `mascope.reference.compounds(name, version=None)`
  returns every compound of a source's active version, or of the one named,
  paging through the API. Any role reads both.

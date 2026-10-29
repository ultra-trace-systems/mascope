- **A 409 from a sample's run import says when it applied nothing.** The
  import route and its delete answer a request that met another one still
  running on the sample with the code `assignment_claim_held` in `detail`:
  the same request goes through once the other finishes, unlike a 409 over
  the run's state.

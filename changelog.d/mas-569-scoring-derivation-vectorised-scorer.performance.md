- **The assignment engine scores its candidates in bulk.** Stage A's fit over
  every matched ion, the seeded re-score of the batch search and the
  inspector, and the composition finder's ranking of each peak's readings now
  score all their envelopes in one pass instead of one call per ion. The
  scores are the ones the one-at-a-time path gave, to the rounding of a sum.

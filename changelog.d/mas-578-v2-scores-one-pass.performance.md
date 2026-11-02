- **The composition search scores its candidates in one pass.** With peak
  assignment on, the fit score of every candidate an m/z search finds is
  computed in one call instead of one per candidate, as the assignment engine
  already does; the scores are unchanged. The targeted match under
  `MASCOPE_MATCH_SCORE_VERSION=2` scores its ions the same way.

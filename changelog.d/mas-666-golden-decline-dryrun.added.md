- **The AI toolkit's golden fixture has traps, and its checker is checked.**
  `libraries/ai/tests/ai_golden.py` gains a `decline` question class whose
  known answer is "not available" with the reason (a batch that does not
  exist, a workspace the token cannot see, an m/z outside the acquired range,
  a quantity no Frame carries, a column the peaks Frame lacks), two analysis
  shapes (the top ions by median height; a compound named only if the data
  gives the name), and a `dryrun` command that tries the checker on scripted
  answers before any model runs: the known answer must pass, every
  fabrication of it must be wrong, no answer must be missing. A model run
  records correct / wrong / missing / sloppy, with provider errors counted
  apart (`golden_transcripts/README.md`). The shapes are those of the
  in-house evaluation of open models (`docs/dev/ai_analyst_shell.md`,
  section 3.4).

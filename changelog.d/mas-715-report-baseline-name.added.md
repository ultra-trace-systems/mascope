- **The security assessment report names the baseline file it read.**
  `pentest-report.json` gains a `baseline` field, and `pentest-report.md` and
  the console summary a **Baseline** line, with the file's name only. A runner
  that names the wrong file, or a suite copy that ignores
  `MASCOPE_PENTEST_BASELINE`, now shows in the archived evidence.

- **The smoke test no longer fails at random on a page it was served.**
  `tooling/smoke-test.sh`, the release gate and the packaged-smoke check,
  piped some pages into `grep -q`, which could cut the download off and fail
  the check. Each check now reads the whole page first and then matches on it.

- **`mascope-cli` no longer publishes a `dev` extra.** It named a package
  that is not on PyPI, so `pip install "mascope-cli[dev]"` could not
  resolve. The developer commands it served run only in a source checkout,
  which installs their packages itself. CI now checks the CLI's declared
  dependencies with `deptry`.

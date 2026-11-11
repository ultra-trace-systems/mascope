- **The assignment bench builds its instance from a sets manifest.**
  `tooling/assignment_bench/bench.py init <manifest>` binds env `bench` to a
  free instance slot and starts a ref's backend there (develop by default).
  It seeds each set's ionization mode and collections through the API and the
  shipped reference lists, uploads the sets' files, and checks that every
  sample resolved its set's mode and has peaks. It records each sample's id
  under its set in `state.json`. A second `init` over the same manifest does
  nothing; `--rebuild` starts over, and `status` lists the sets. A set's files
  are demo bundle samples or fleet corpus objects.
  `manifests/demo.json` holds the public demo bundle's bromide and uronium
  samples as sets `Db` and `Du` (`tooling/assignment_bench/README.md`).
  `mascope_cli.instance.resolve_or_allocate` takes the env name of a new
  binding (`env=`), which the bench uses.

- **The demo bundle's goldens come out in the same order on every rebuild.**
  Two target ions of one formula, such as an oxygenate's `[M+NH4]+` and an
  amine's `[M+H]+`, each get a match row with the same score. The ions and
  peaks goldens now order such a pair by its target id. Before, the pair came
  out in whichever order the database read it, so two rebuilds of one bundle
  wrote different `expected/ions.parquet` files from identical results.

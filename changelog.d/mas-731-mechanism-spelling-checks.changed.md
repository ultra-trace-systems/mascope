- **One spelling for a doubly charged ion, and no misleading ones.** The
  Ionization Mechanisms tab saves `[M+H+H]2+` as `[M+2H]2+`, so the same ion
  cannot be added twice. It refuses `[M+H]2+` and `[M+H2]2+` with a message
  that names `[M+2H]2+`: a proton, an alkali metal, a halide, ammonium, nitrate
  or silver carries one charge. It also refuses a removed proton beside an
  added species (`[M+H2O-H]-`), which is not a neutral loss. `[M+Ca]2+`,
  `[M+H+H+Na]3+`, `[M+H-H2O]+` and `[M-H-H2O]-` read as before.

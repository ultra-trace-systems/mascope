- **A dimer, a doubly charged ion and a neutral loss as ionization mechanisms.**
  The Ionization Mechanisms tab now accepts `[2M+H]+`, `[M+2H]2+` (or `[M+Ca]2+`)
  and `[M+H-H2O]+`, shows under the field the ion a typed mechanism makes, and
  takes an optional name. Target ions are built for them at the right m/z;
  the composition search and peak assignment leave them out for now. Every
  mechanism is also stored as a structured row (operation, species, its
  charge, the molecule count, the neutral loss), which the API reports with
  the ion's charge and mass shift. The upgrade stops on a stored mechanism
  that reads as neither notation and names it: correct or delete that row,
  then upgrade again (`docs/maintaining.md`, "Mechanism notation").

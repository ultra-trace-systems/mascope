- **`mascope prod mfa status` shows machine accounts as `exempt`.** A File
  Agent device's machine account never signs in interactively and is exempt
  from the two-factor policy, but the listing printed it as `off`, a gap in
  two-factor coverage that could never be closed. Person accounts still show
  `on` or `off`.

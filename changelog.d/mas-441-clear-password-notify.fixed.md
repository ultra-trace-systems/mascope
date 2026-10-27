- **Withdrawing a forced password change reaches open tabs at once.** The
  `clear_password_change_requirement` script now tells each released account's
  open tab, which leaves the password screen without a reload, and refreshes
  open user-management tables, as requiring the change already did.

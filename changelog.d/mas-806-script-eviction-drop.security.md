- **The password-change scripts no longer report success over evictions they dropped.**
  When Redis stopped answering while `require_password_change` or
  `clear_password_change_requirement` ran, the evictions it missed were kept
  for a retry that the script's exit cancelled, with no word: the operator read
  that open sessions moved at once while the armed accounts' sockets kept
  their rooms. The scripts now try those evictions again for up to three retry
  intervals before they disconnect, and say with an error how many they had to
  drop.

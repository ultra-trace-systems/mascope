- **A fleet rollout names the servers whose units are outdated.**
  `tooling/fleet/update.yml` prints a note for a server whose doctor flags its
  systemd units or their copied scripts `OUTDATED` after the update, and says
  to re-run `mascope prod units install` there. The rollout carries on as
  before: the flag does not change the doctor's exit code.

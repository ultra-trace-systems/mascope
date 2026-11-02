- **A wheel install deploys the release its CLI was cut with.** Each CLI
  version now names its Mascope release, and the release manifest names the
  CLI cut with it (`cli_version`). Without a source checkout, `mascope prod up`
  and `mascope prod update` deploy that release and refuse any other,
  `latest` included, naming the CLI to install for it
  (`uv tool install mascope-cli==<calver>`). Before, an unpinned wheel install
  followed `latest` on the compose file of whatever CLI wrote its home. The
  first `prod up` after a CLI upgrade writes the new release's config layers
  and compose files over the home's, keeping a copy under
  `.runtime/replaced/`; the env overlay and the secrets are never touched.
  `prod update --auto` exits with code 40 when an update is due that only a
  newer CLI can apply, and names it in `.runtime/update/cli-to-install`. A
  checkout install is unchanged.

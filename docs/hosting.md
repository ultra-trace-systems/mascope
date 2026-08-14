# Hosting & deployment

How to run Mascope, from a one-machine trial to a shared production deployment.

## Managed hosting by Ultra Trace

The simplest option is to let Ultra Trace host Mascope for you - no servers, updates,
backups, or TLS to manage. Contact [sales@ultratrace.eu](mailto:sales@ultratrace.eu) for a
quote.

## Self-hosting

Mascope ships as Docker images on GHCR, orchestrated with Docker Compose.

### Local trial (one machine)

Run the one-command demo stack on `localhost` - no TLS, no build, just pull and
run; it comes up preloaded with the demo dataset. See
[Getting started](user/getting-started/index.md). The web UI is served at
`http://localhost:8080`; loopback is a browser secure context, so everything
(including clipboard) works over plain HTTP.

### Production (shared / LAN, over HTTPS)

For a deployment reached over the network by multiple users, serve over
**HTTPS** - browsers only treat `localhost` as a secure context over plain HTTP,
so over a LAN address features like clipboard access require HTTPS.

Mascope is deployed from a clone of the repo: the `mascope` CLI drives Docker
Compose, and the **checked-out git tag selects which release runs** (it sets
`MASCOPE_VERSION`, which picks the image tag to pull and the version the UI
reports). The same holds when the boot service starts the stack - it runs from
the checkout - so a reboot brings back the release the checkout is on. Verify
with `mascope prod doctor`, which flags any mismatch between the running images
and the checkout.

#### Set up (run the latest release)

1. **Install prerequisites** on the host: Docker + Docker Compose, `git`, and
   [uv](https://docs.astral.sh/uv/).
2. **Get Mascope and pin the release.** Pick the latest version from
   [Releases](https://github.com/ultra-trace-systems/mascope/releases):

   ```sh
   git clone https://github.com/ultra-trace-systems/mascope.git
   cd mascope
   git fetch --tags
   git checkout v1.0.0           # the release you want to run
   ./tooling/ubuntu.sh install   # installs the `mascope` CLI (Ubuntu)
   ```

3. **Create the secrets** in `.runtime/secrets/`:

   ```sh
   mkdir -p .runtime/secrets
   head -c 32 /dev/urandom | xxd -p -c 32 > .runtime/secrets/postgres_password.txt
   head -c 32 /dev/urandom | xxd -p -c 32 > .runtime/secrets/jwt_secret_key.txt
   head -c 32 /dev/urandom | xxd -p -c 32 > .runtime/secrets/server_owner_secret_key.txt
   ```

4. **Set up TLS** - pick the option that fits your audience:
   - **Self-signed** (`mascope cert gen` writes `mascope.app.pem`/`.key` into
     `.runtime/secrets/`): works immediately; each user clicks through a one-time
     browser warning. Make sure the certificate's SAN matches the hostname/IP.
   - **Internal CA** (e.g. [mkcert](https://github.com/FiloSottile/mkcert) or an
     org CA): warning-free on the LAN; install the CA on client machines once,
     then issue a certificate for the server's hostname.
   - **Real certificate** via a domain + Let's Encrypt **DNS-01** (a reverse
     proxy such as Caddy or Traefik automates issuance/renewal): trusted, no
     warnings, no client setup. DNS-01 does not require exposing the server to
     the internet.

5. **Pull the release images and start:**

   ```sh
   mascope prod docker pull   # pulls the v1.0.0 images from GHCR
   mascope prod up
   ```

   `db_init` creates the database and applies migrations before the app starts.
   Open `https://<host>` and register the first owner account (with `server_owner_secret_key`).

   The deployment serves the user documentation from the same host at
   `https://<host>/docs/` - it is bundled into the frontend image, so no extra
   setup is needed.

#### Update to a new release

```sh
cd mascope
git fetch --tags
git checkout v1.1.0          # the new release tag
mascope prod docker pull     # pulls the v1.1.0 images
mascope prod up              # recreates the containers
```

On start, `db_init` takes a pre-migration backup and applies any pending
migrations. To roll back, check out the previous tag and repeat
`mascope prod docker pull && mascope prod up` (restore the pre-migration backup
if a migration had run).

`mascope prod update` does the pull-and-recreate in one step, and
`mascope prod update --check` tells you up front whether a release carries a
database migration (downtime) or not. Servers can also update themselves on a
schedule - see the [maintainer runbook](maintaining.md).

#### Persistence & backups

State lives under `.runtime/` (PostgreSQL data + the filestore) - back it up.
`mascope prod db backup` takes a manual dump; see the database section of the
developer guide.

For the full operations reference - provisioning, updates, backups, and
troubleshooting - see the [maintainer runbook](maintaining.md). For deployment
internals (runtime, database tuning, env sync) see the
[developer guide](dev/developer_guide.md).

# Run the demo

Already have a Mascope to log in to? Start with
[First steps in the app](first-steps.md) instead.

The fastest way to see Mascope is to run it on your own machine with Docker,
preloaded with the demo dataset. This is a local, single-machine setup served
over `http://localhost` - no certificates, no account signup.

!!! info "Prerequisites"
    [Docker](https://docs.docker.com/get-docker/) (Desktop or Engine) running.
    That is the only requirement for the local trial.

## Run the demo (real data, one command)

Download the demo compose file and start it:

```sh
curl -O https://raw.githubusercontent.com/ultra-trace-systems/mascope/master/docker-compose.demo.yaml
docker compose -f docker-compose.demo.yaml up
```

It pulls the published images and loads the published demo dataset - a real,
de-identified Orbitrap time series with samples, batches, and matches - before
the app starts. When it is up, open **http://localhost:8080** and log in with:

- **Email:** `demo@mascope.app`
- **Password:** `mascope-demo`

The first run downloads ~150 MB. Tear it down with
`docker compose -f docker-compose.demo.yaml down -v`.

For what is in the bundle (and how it is built and published), see the
[demo dataset guide](https://github.com/ultra-trace-systems/mascope/blob/master/docs/demo_dataset.md).

To run Mascope for real - with your own data, on a server, over HTTPS - see
[Hosting & deployment](https://github.com/ultra-trace-systems/mascope/blob/master/docs/hosting.md).

## Next steps

- [First steps in the app](first-steps.md) - find your way around the UI the demo just opened.
- [Concepts](../concepts/index.md) - the domain model (samples, batches, matching, calibration).
- [Guides](../guides/index.md) - task-by-task how-tos.
- Sharing Mascope with a team on a LAN, or production deployment: see
  [Hosting & deployment](https://github.com/ultra-trace-systems/mascope/blob/master/docs/hosting.md).

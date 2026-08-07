# Mascope

**A platform for analysing and storing high-resolution mass spectrometry data** - import
instrument files, browse samples and batches, run targeted matching and
calibration, and explore results in the web UI or from Python.

<picture>
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/mascope-ui-light.png">
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/mascope-ui-dark.png">
  <img alt="Mascope web UI" src="docs/assets/mascope-ui-dark.png">
</picture>

Mascope ingests Thermo Orbitrap (`.raw`) and Tofwerk TOF (`.h5`) data, processes it
through a peak-detection + calibration + targeted-matching pipeline, and serves
it through a multi-user web application and a Python SDK. It is built for
laboratories that need reproducible, high-throughput analysis of complex spectra.

## Try it in 5 minutes

Run Mascope on your machine **with real data to explore**. Docker is the only
prerequisite. One file, one command:

```sh
curl -O https://raw.githubusercontent.com/ultra-trace-systems/mascope/master/docker-compose.demo.yaml
docker compose -f docker-compose.demo.yaml up
```

It pulls the published images and loads the published [demo dataset](docs/demo_dataset.md).
When it's up, open <http://localhost:8080> and log in with:
**`demo@mascope.app`** / **`mascope-demo`**.
The first run downloads ~150 MB; tear it down with
`docker compose -f docker-compose.demo.yaml down -v`.

To run Mascope for real - your own data, on a server, over HTTPS - see
[Hosting & deployment](docs/hosting.md). Contributors with the repo cloned can
also run the demo from source with `mascope demo` (see
[Demo dataset](docs/demo_dataset.md)).

## Hosting

- **Managed** - let Ultra Trace run Mascope for you, with no infrastructure to manage.
  Contact [sales@ultratrace.eu](mailto:sales@ultratrace.eu) for a quote.
- **Self-host** - Mascope ships as Docker images. Try it locally (above), or see
  [Hosting & deployment](docs/hosting.md) for sharing it on a LAN or in
  production (HTTPS, TLS options, secrets, backups, upgrades).

## Tech stack

| Layer                  | Technologies                                                                                       |
| ---------------------- | -------------------------------------------------------------------------------------------------- |
| **Backend**            | Python, FastAPI, Uvicorn, Socket.IO, PostgreSQL 16, SQLAlchemy 2 (async), Alembic, Redis, Pydantic |
| **Frontend**           | Vue 3, PrimeVue, Vite, served by nginx                                                             |
| **SDK**                | Python (`mascope_sdk`) for notebooks and scripts                                                   |
| **Instrument readers** | OpenTFRaw (Thermo `.raw`), h5py (Tofwerk `.h5`)                                                    |
| **Tooling & deploy**   | uv workspace, Docker / Docker Compose, GHCR images, the `mascope` CLI                              |

## Documentation

| For                                | Where                                                                                           |
| ---------------------------------- | ----------------------------------------------------------------------------------------------- |
| **Users** (scientists, operators)  | [User docs](docs/user/index.md) (MkDocs site under `docs/user/`)                                |
| **SDK / notebook users**           | [SDK readme](libraries/sdk/README.md)                                                           |
| **Instrument PCs** (automatic upload) | [File Agent](docs/user/instruments/index.md#the-file-agent)                                  |
| **Developers / contributors**      | [Developer guide](docs/dev/developer_guide.md) (build, run, runtime, backend, database, deploy) |
| **Hosting & deployment**           | [Hosting](docs/hosting.md) (managed, local, LAN/production)                                     |
| **Demo dataset & reproducibility** | [Demo dataset](docs/demo_dataset.md)                                                            |

## Community

Questions, help, and discussion happen on Discord - come say hello:

[![Discord](https://img.shields.io/discord/1221735590890967070?logo=discord&logoColor=white&label=Discord&color=5865F2)](https://discord.gg/R5kEKJcKe8)

## License

[Apache-2.0](LICENSE). See [NOTICE](NOTICE) for attributions.

## Citation

If you use **Mascope** in your research, please cite the software using the
metadata in [`CITATION.cff`](CITATION.cff) (GitHub's "Cite this repository"
button) or the archived Zenodo release:

[![DOI](https://img.shields.io/badge/DOI-10.5281%2Fzenodo.21037634-blue)](https://doi.org/10.5281/zenodo.21037634)

The bundled **demo dataset** has its own DOI:

> Mascope demo dataset at Zenodo: https://doi.org/10.5281/zenodo.20929489

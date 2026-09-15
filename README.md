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
  Contact [hello@ultratrace.eu](mailto:hello@ultratrace.eu) for a quote.
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

## Ecosystem

- **[Peaky](https://github.com/ultra-trace-systems/peaky)** - an AI-native analysis
  toolbox built on the Mascope SDK: untargeted chemical-formula assignment,
  time-series clustering, Van Krevelen plots, and PDF reports, driven in plain
  language through a coding agent such as [Claude Code](https://claude.com/claude-code).
  It is the SDK-powered, AI-driven power-user path - a complement to the web app,
  with Mascope as the system of record and Mascope's scoring as the only scorer.

## Community

Questions, help, and discussion happen on Discord - come say hello:

[![Discord](https://img.shields.io/discord/1221735590890967070?logo=discord&logoColor=white&label=Discord&color=5865F2)](https://discord.gg/R5kEKJcKe8)

## License

[Apache-2.0](LICENSE). See [NOTICE](NOTICE) for attributions. Contributions are
accepted under the
[Individual Contributor License Agreement](https://github.com/ultra-trace-systems/cla/blob/main/ICLA.md)
(see [CONTRIBUTING.md](CONTRIBUTING.md#contributor-license-agreement)).

## Citation

If you use **Mascope** in your research, please cite the accompanying paper:

> Shcherbinin, A., Shen, J., Kausiala, O., Tumashevich, K., Chernonog, P., Zorn,
> S., Nölscher, A., Petäjä, T., Kulmala, M., Kangasluoma, J., Thakur, R. C.,
> Sarnela, N., Ehn, M., Sipilä, M., Finkenzeller, H., Rissanen, M., & He, X.-C.
> (2026). Networked Open-Source Infrastructure for Scalable Analysis of Chemical
> Ionization Mass Spectrometry Data. *ChemRxiv*.
> https://doi.org/10.26434/chemrxiv.15005713/v1

[![ChemRxiv](https://img.shields.io/badge/ChemRxiv-10.26434%2Fchemrxiv.15005713-b31b1b)](https://doi.org/10.26434/chemrxiv.15005713/v1)

You can also cite the software directly using the metadata in
[`CITATION.cff`](CITATION.cff) (GitHub's "Cite this repository" button) or the
archived Zenodo release:

[![DOI](https://img.shields.io/badge/DOI-10.5281%2Fzenodo.21037634-blue)](https://doi.org/10.5281/zenodo.21037634)

The bundled **demo dataset** has its own DOI:

> Mascope demo dataset at Zenodo: https://doi.org/10.5281/zenodo.20929489

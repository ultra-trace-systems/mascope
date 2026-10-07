# Licence texts kept for the notices

`tooling/third-party-notices.py` writes out the licence files each package
ships. These are the texts for what is redistributed without one, kept here so
that the notices carry them all the same.

| File | What it is | Taken from |
|---|---|---|
| `cpython-3.12.rst` | CPython's own account of the software built into the interpreter and its standard library: OpenSSL, libffi, Expat, zlib, libmpdec and the rest. The `LICENSE.txt` in an interpreter's installation names some of these or none, depending on who built it, and a program that carries the interpreter carries all of them. | `Doc/license.rst` in `python/cpython` at tag `v3.12.10`, unchanged |
| `loguru@0.7.3.txt` | loguru's licence. Its wheel holds no licence file. | `LICENSE` in `Delgan/loguru` at tag `0.7.3`, unchanged |

## How they are found

- `<name>@<version>.txt`, the name as `uv.lock` spells it: read for an
  installed distribution of exactly that version that ships no licence file.
  A new version comes back for a look, because the File Agent's notices are
  refused for a package that has neither.
- `cpython-<major>.<minor>.rst`: read with `--interpreter`, for that Python.
  The generator refuses to write the interpreter's entry without it.

The texts are not to be edited. Replace a file when the version it is for is
gone, and delete it when the package ships its own.

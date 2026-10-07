# Mascope File Agent

The File Agent runs on an instrument computer, watches a folder, and uploads
each new data file in it to a [Mascope](https://github.com/ultra-trace-systems/mascope)
server. It pairs the machine with the server, renews its own credential,
retries an upload that fails, and logs what the server made of each file.

## The program

Instrument computers run the File Agent as a Windows program, installed from
`Mascope-File-Agent-Setup.exe` on the
[Mascope releases page](https://github.com/ultra-trace-systems/mascope/releases).
The [instrument guide](https://github.com/ultra-trace-systems/mascope/blob/master/docs/user/instruments/index.md)
covers installing, pairing and configuring it. Nothing on this page is needed
for that.

## The library

This package is the same agent as a Python library, for a program that already
runs on the instrument computer and wants the uploads in its own process.

```sh
pip install mascope-file-agent
```

```python
from mascope_file_agent import Agent, AgentSettings, identity

config_path = r"C:\Users\lab\AppData\Roaming\Mascope\FileAgent\config.toml"
settings = AgentSettings.from_file(config_path)

# Once per process, before pairing and before an agent starts: the service the
# machine is paired as, and the version the server shows for it.
identity("file-agent", version="1.2.3", verify_tls=settings.verify_tls)

agent = Agent(settings, persist_token=settings.token_writer(config_path))
agent.start()  # returns; the agent runs on threads of its own
...
agent.stop(timeout=30)
```

- `AgentSettings` holds the keys of the `[file-agent]` section of the File
  Agent's `config.toml`, so a configuration written by the program is read as
  it is. `AgentSettings(host=..., access_token=..., source=..., instrument=...)`
  builds one without a file.
- `Agent.start()` returns at once and `Agent.run_until_complete()` blocks until
  interrupted. `Agent.stop(timeout)` waits for the uploads under way, gives up
  on the rest when the time runs out, and returns whether everything ended.
  `Agent.running` says whether it is still at work. An agent runs once; build
  another to run again.
- `Agent.on_ready(callback)` calls `callback(path)` with each complete file
  before it is uploaded, one file at a time, on the watcher's thread. A step
  that raises is logged, and the file is uploaded all the same.
- A file's acquisition record goes with its upload: a JSON document the
  program writes beside the file, `<file>.mascope.json`, saying which
  step of which run acquired it and under which chemistry. Write it in
  an `on_ready` step and it is there when the file is uploaded.
  `mascope_sdk.acquisition` holds the schema: build an
  `AcquisitionRecord` and write `acquisition.dump(record)`. A record
  the agent cannot use, or a server too old to keep one, never keeps
  the file from being uploaded.
- `logger=` takes anything with the methods of a standard `logging.Logger`;
  without one the agent logs to the `mascope_file_agent` logger.
- `repair=` decides what happens when the server refuses the machine's
  credential. The default only says so in the log; `ConsoleRepair()` asks at
  the console and pairs there, as the program does. Subclass `Repair` to show
  the refusal in a user interface.
- `persist_token=` is called with each renewed access token.
  `settings.token_writer(path)` rewrites that `config.toml`, which suits a
  file holding nothing but the `[file-agent]` section.

Only one agent may watch a folder: two would upload every file twice.

The guided setup the program runs on first start is
`mascope_file_agent.wizard.run_setup_wizard`, which takes the current settings
as a dict and returns the completed ones for
`mascope_file_agent.config.write_user_config`.

The package also installs a `mascope-file-agent` command. It is the entry
point the Windows program is built from, and outside that build it expects a
Mascope development checkout around it.

## Versions

The library is versioned by date (`2026.10.7`), like Mascope's other Python
packages. The Windows program reports the Mascope release it was built for
(`v1.10.1`).

## Licence

Apache-2.0.

# Getting started with Python

A step-by-step guide for researchers who are new to Python. By the end, you'll
have a working environment and be loading data from Mascope into interactive
notebooks. The setup takes about 15 minutes.

!!! tip "Already comfortable with Python?"
    Skip this guide and head straight to the [SDK overview](index.md).

## Why Python?

Python has become the de facto standard for scientific computing and data
analysis. Its ecosystem, including libraries like NumPy, pandas, and SciPy, is
unmatched for working with tabular data, statistics, machine learning and
visualization. Most new tools, tutorials, and research workflows in the data
science community are built around Python.

The Mascope SDK lets you tap into that ecosystem. Instead of being limited to
the analyses available in the Mascope application, you can pull your mass
spectrometry data into Python and build any custom analysis pipeline you need:
filter, transform, visualize, export, or feed data into machine learning
models.

## Install VS Code

VS Code is a free, lightweight code editor that can run Jupyter notebooks:
interactive documents that mix code, output, and text. This is the recommended
way to use the Mascope SDK. The VS Code ecosystem includes an extension library
with many useful plugins, for example to visualize data frames interactively.

1. Download VS Code from [code.visualstudio.com](https://code.visualstudio.com/)
   and install it.

2. Open VS Code and install the required extensions. Click the **Extensions**
   icon in the left sidebar (or press `Ctrl+Shift+X` / `Cmd+Shift+X`), then
   search for and install:

    - **Python** (by Microsoft)
    - **Jupyter** (by Microsoft)

    Optionally (but recommended) also install:

    - **Data Wrangler** (by Microsoft) for exploring DataFrames visually

![Install the Python extension](images/vscode-extension-python.png)

![Install the Jupyter extension](images/vscode-extension-jupyter.png)

![Install the Data Wrangler extension](images/vscode-extension-datawrangler.png)

## Create a project folder

Create a folder on your computer where your analysis work will live. You can do
this in your file explorer or from the terminal:

=== "Windows"

    ```
    mkdir C:\Users\YourName\mascope-analysis
    cd C:\Users\YourName\mascope-analysis
    ```

=== "macOS"

    ```
    mkdir ~/mascope-analysis
    cd ~/mascope-analysis
    ```

Then open this folder in VS Code: **File → Open Folder...** and select it.

![Empty project folder in VS Code](images/vscode-empty-project.png)

## Install Python and the Mascope SDK

To set up the Python environment, we'll use **uv**: a modern Python package
manager that can install Python itself, create virtual environments, and manage
packages, all in a few commands. You don't need to install Python manually (but
it's fine if you have it installed already).

!!! info "What is a virtual environment?"
    It's an isolated space for your project's packages, ensuring that
    installing or updating packages for one project doesn't break another.
    Think of it as giving each project its own clean, independent set of tools.

### Install uv

=== "Windows (PowerShell)"

    ```
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    ```

=== "macOS (Terminal)"

    ```
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```

Close and reopen your terminal after installing, then verify: `uv --version`.

### Initialize your project and install the SDK

Open a terminal **inside VS Code** (press `` Ctrl+` `` or go to
**Terminal → New Terminal**) and run:

```
uv init --python 3.14
uv add "mascope_sdk[examples]"
```

This does three things automatically:

1. Downloads and installs Python 3.14 (if not already present)
2. Creates a virtual environment (`.venv` folder) in your project
3. Installs the Mascope SDK with all its dependencies - the `[examples]`
   extra also brings the plotting and analysis libraries (plotly, matplotlib,
   numpy, scipy) used by the tutorial notebooks

![uv init and install](images/uv-init-install.png)

### Select the interpreter in VS Code

VS Code needs to know which Python to use. Press `Ctrl+Shift+P` (or
`Cmd+Shift+P` on macOS) to open the Command Palette, type
**"Python: Select Interpreter"**, and choose the one inside your `.venv`
folder.

![Open the interpreter picker](images/vscode-select-interpreter.png)

![Select the .venv interpreter](images/vscode-select-mascope-analysis.png)

!!! tip
    Once selected, VS Code remembers this setting for the project. You won't
    need to do it again unless you recreate the environment.

## Configure your credentials

The SDK needs two things to connect to your Mascope instance: the **server
URL** and an **API token**.

### Generate an API token

1. Log in to your Mascope instance in the browser.
2. Open the **Home menu** (top-left corner).
3. Go to the **Settings** tab.
4. Generate a new API token for Jupyter Notebooks and copy it.

![Open the Home menu](images/mascope-home-menu.png)

![Generate an API token in Mascope](images/mascope-api-token.png)

!!! warning "Keep your token private"
    Treat it like a password. Do not share it or commit it to version control.
    To expire an existing token, you can simply generate a new one.

### Create a `.env` file

In your project folder, create a new file called `.env` (note the leading dot).
You can do this in VS Code: right-click in the Explorer sidebar →
**New File** → name it `.env`.

Add the following lines, replacing the placeholder values with your actual URL
and token:

```env
MASCOPE_URL=https://your-instance.mascope.app
MASCOPE_ACCESS_TOKEN=your-api-token-here
```

The SDK reads this file automatically when you create a client - no need to
paste credentials into your code.

![The .env credentials file](images/vscode-env-file.png)

## Run your first notebook

The SDK ships with tutorial notebooks that demonstrate common workflows. Let's
copy them into your project and run the first one.

### Copy the example notebooks

Open a terminal in VS Code and run:

```
python -c "import mascope_sdk; mascope_sdk.copy_examples()"
```

This creates a `mascope_examples/` folder with several `.ipynb` notebook files.

![Copying the example notebooks](images/vscode-copy-example-notebooks.png)

!!! warning "Make sure the project's virtual environment is active"
    Before running the python command, check that the prompt starts with
    `(mascope-analysis)`. In case it does not,
    [select the correct interpreter](#select-the-interpreter-in-vs-code) and
    restart the VS Code terminal.

### Open the first notebook

In the VS Code Explorer, navigate to `mascope_examples/` and open
`01_getting_started.ipynb`.

![The getting started notebook](images/vscode-notebook-open.png)

### Select the notebook kernel

When you open a notebook for the first time, VS Code may ask you to select a
kernel (the Python environment that runs the code). Click **"Select Kernel"**
in the top-right corner and choose the `.venv` interpreter you set up earlier.

![Select the notebook kernel](images/vscode-select-kernel.png)

### Run the cells

Click the **play button** (▶) next to the first cell, or place your cursor in
the cell and press `Shift+Enter` to run it.

![Run the first cell](images/vscode-run-cell.png)

!!! note
    When you run a notebook cell for the first time, VS Code may prompt you to
    install the `ipykernel` and `pip` packages, which are required to run
    Jupyter notebooks. If the prompt appears, **click "Install"** (the
    `[examples]` extra already includes `ipykernel`, so usually it does not).

![Install ipykernel and pip](images/vscode-install-ipykernel.png)

When running the first cell initializing the Mascope client, you should see
output showing it has successfully loaded the available workspaces. If you
don't, check the [troubleshooting section](#troubleshooting).

![Running a notebook cell](images/vscode-notebook-output.png)

Work through the cells from top to bottom. Each cell builds on the previous
one.

You should see output from each cell: DataFrames with your workspace and batch
data, sample listings, and eventually a spectrum plot. **Congratulations,
you're all set up!**

### What's next?

Now that you're set up, work through the tutorial notebooks in order:

| #   | Notebook                           | Topic                                                     |
| --- | ---------------------------------- | --------------------------------------------------------- |
| 1   | `01_getting_started.ipynb`         | Connect, list workspaces/batches/samples, view a spectrum |
| 2   | `02_batch_timeseries.ipynb`        | Load peaks across batches, filter, and plot               |
| 3   | `03_intra_sample_timeseries.ipynb` | Per-scan intensity timeseries for specific compounds      |
| 4   | `04_mass_defect_plot.ipynb`        | Mass defect visualization                                 |
| 5   | `05_peaks_by_stage.ipynb`          | Compare measurement stages within a single sample         |
| 6   | `06_normalization.ipynb`           | Normalize intensities by TIC or reagent-ion signal        |
| 7   | `07_background_subtraction.ipynb`  | Subtract a background sample (matched ions or m/z bins)   |
| 8   | `08_correlation_analysis.ipynb`    | Find co-varying peaks via correlation and clustering      |
| 9   | `09_composition_assignment.ipynb`  | Assign elemental compositions to unmatched peaks          |
| 10  | `10_batch_stages.ipynb`            | Split a batch into stages and compare per-stage averages  |

For full API documentation and advanced usage, see the
[SDK overview](index.md).

## Troubleshooting

### `uv` is not recognized

The uv installer didn't add itself to your PATH, or you haven't reopened your
terminal yet. Close and reopen VS Code (or your terminal), then try
`uv --version` again.

### `No module named 'mascope_sdk'`

The SDK isn't installed in the active environment. Make sure:

1. You ran `uv add mascope_sdk` inside your project folder.
2. VS Code is using the correct interpreter. It should point to the `.venv`
   folder in your project (check the bottom status bar).

### `ConfigurationError: Missing MASCOPE_URL or MASCOPE_ACCESS_TOKEN`

The SDK can't find your credentials. Verify that:

- The `.env` file is in your project's root directory (the folder you opened
  in VS Code).
- The variable names are spelled correctly: `MASCOPE_URL` and
  `MASCOPE_ACCESS_TOKEN`.
- There are no extra spaces around the `=` sign.

### `AuthenticationError` or `401` response

Your API token is invalid or expired. Generate a new one in your Mascope
account settings.

!!! note
    Every time you edit the `.env` file, you will need to restart the Jupyter
    kernel for the changes to take effect.

### Notebook cells show `[*]` and never finish

The kernel may be stuck. Try: **Kernel → Restart** from the notebook toolbar,
then re-run the cells.

### Something else?

Check the [SDK overview](index.md) for additional configuration options, or
reach out to your Mascope administrator.

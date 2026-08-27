"""
Test command for running and managing Mascope tests.

This module provides a CLI interface for running different types of tests
in the Mascope project. It supports different test components (backend, frontend)
and test modules (unit, integration, system).

Usage:
    mascope test run              # Run all backend tests
    mascope test run -m unit      # Run only unit tests
    mascope test run -n dataset_model # Run specific test by name
    mascope test list             # List available tests
"""

import os
from enum import Enum

import typer
from typing_extensions import Annotated

import mascope_cli.cmd.lib as lib
from mascope_cli.checkout import source_checkout
from mascope_cli.runtime import runtime


test_app = typer.Typer()


class TestComponent(str, Enum):
    """Components of the Mascope application that can be tested"""

    BACKEND = "backend"
    FRONTEND = "frontend"
    LIBRARIES = "libraries"


class TestModule(str, Enum):
    """Types of test modules available in the Mascope test suite"""

    UNIT = "unit"
    INTEGRATION = "integration"
    SYSTEM = "system"
    MIGRATIONS = "migrations"
    MASCOPE_SDK = "sdk"
    MASCOPE_TOOLS = "tools"
    MASCOPE_FILE = "file"
    MASCOPE_MATCH = "match"
    MASCOPE_SIGNAL = "signal"
    MASCOPE_THERMO = "thermo"
    ALL = "all"


@test_app.callback()
def main():
    """Run tests for Mascope components

    This command group provides tools for running and listing tests for
    different components of the Mascope application.
    """


@test_app.command()
def run(
    components: Annotated[
        list[TestComponent] | None,
        typer.Argument(
            help=f"Components to test [{', '.join([c.value for c in TestComponent])}]",
            show_default=f"{TestComponent.BACKEND.value}",
        ),
    ] = None,
    module: Annotated[
        TestModule | None,
        typer.Option(
            "--module",
            "-m",
            help=f"Test module type [{', '.join([m.value for m in TestModule])}]",
            case_sensitive=False,
        ),
    ] = None,
    test_name: Annotated[
        str | None,
        typer.Option(
            "--name",
            "-n",
            help="Run a specific test by name",
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Run tests with verbose output",
        ),
    ] = False,
):
    """Run tests for specified Mascope components

    By default, this runs all backend tests. You can specify which components to test
    by providing them as arguments.

    Examples:\n
      mascope test run                      # Run all backend tests\n
      mascope test run -v                   # Run tests with verbose output\n
      mascope test run -m unit              # Run only unit tests\n
      mascope test run -n dataset_model     # Run a specific test by name\n
      mascope test run -m migrations        # Run Alembic migration tests\n
    """
    # Default to backend if no components specified
    if not components:
        if module in (
            TestModule.MASCOPE_SDK,
            TestModule.MASCOPE_TOOLS,
            TestModule.MASCOPE_FILE,
            TestModule.MASCOPE_MATCH,
            TestModule.MASCOPE_SIGNAL,
            TestModule.MASCOPE_THERMO,
        ):
            components = [TestComponent.LIBRARIES]
        elif module in (
            TestModule.UNIT,
            TestModule.INTEGRATION,
            TestModule.SYSTEM,
            TestModule.MIGRATIONS,
        ):
            # Backend-specific module — don't run libraries by default.
            components = [TestComponent.BACKEND]
        else:
            components = [TestComponent.BACKEND, TestComponent.LIBRARIES]

    # Set runtime environment for testing
    runtime.state.mode = "test"

    # Every component runs even when an earlier one failed - a partial answer
    # is worse than a slow one - and the command fails at the end if any did.
    failed = False
    for component in components:
        if component == TestComponent.BACKEND:
            failed = run_backend_tests(module, test_name, verbose) or failed
        elif component == TestComponent.LIBRARIES:
            failed = run_library_tests(module, test_name, verbose) or failed
        elif component == TestComponent.FRONTEND:
            failed = run_frontend_tests(module, test_name) or failed

    if failed:
        raise typer.Exit(1)


def run_frontend_tests(
    module: TestModule | None,
    test_name: str | None,
) -> bool:
    """Run frontend tests with the specified options

    Unit tests (Vitest) run by default; `-m system` runs the hermetic
    end-to-end suite (Playwright), which needs a running stack - by default
    the demo stack at http://localhost:8080 (docker-compose.demo.yaml).
    """
    frontend_dir = os.path.join("server", "frontend")
    # Windows resolves npm as a .cmd shim, which subprocess only finds by
    # its full name (lib.run executes without a shell).
    npm = "npm.cmd" if os.name == "nt" else "npm"

    if module == TestModule.SYSTEM:
        command = f"{npm} run test:e2e"
        if test_name:
            command = f'{npm} run test:only -- "{test_name}"'
    elif module in (TestModule.UNIT, None, TestModule.ALL):
        command = f"{npm} run test:unit"
        if test_name:
            command = f'{npm} run test:unit -- -t "{test_name}"'
    else:
        typer.echo(
            f"Frontend tests support modules 'unit' (Vitest) and 'system' "
            f"(Playwright e2e); got '{module.value}'."
        )
        return False

    typer.echo(f"Running: {command} (in {frontend_dir})")
    return lib.run(command, cwd=frontend_dir).returncode != 0


def run_backend_tests(
    module: TestModule | None,
    test_name: str | None,
    verbose: bool,
) -> bool:
    """Run backend tests with the specified options.

    :return: True when the suite failed, so the caller can fail the command.
    :rtype: bool
    """
    # Base command
    command = ["pytest"]

    # Path to tests - always use forward slashes for paths
    test_path = "server/backend/tests/"

    # Prevent library modules from being treated as backend modules
    if module in (
        TestModule.MASCOPE_SDK,
        TestModule.MASCOPE_TOOLS,
        TestModule.MASCOPE_FILE,
        TestModule.MASCOPE_MATCH,
        TestModule.MASCOPE_SIGNAL,
        TestModule.MASCOPE_THERMO,
    ):
        typer.echo("Module belongs to libraries; running library tests instead.")
        return run_library_tests(module, test_name, verbose)

    # Handle module selection using the enum value
    if module and module != TestModule.ALL:
        test_path = f"{test_path}{module.value}/"

    # If a specific test name is provided, search for it
    if test_name:
        # Check if it's a full path
        if "/" in test_name or "\\" in test_name:
            # Normalize path separators to forward slashes
            test_name = test_name.replace("\\", "/")
            test_path = f"{test_path}{test_name}"
            if not test_path.endswith(".py"):
                test_path += ".py"
        else:
            # Search for the test file
            found = False
            for root, _, files in os.walk(os.path.join("server", "backend", "tests")):
                for file in files:
                    if file.startswith("test_") and file.endswith(".py"):
                        # Extract test name without prefix and extension
                        name_match = file[5:-3]  # Strip "test_" and ".py"
                        if name_match == test_name:
                            # Found the test file - normalize to forward slashes
                            test_path = os.path.join(root, file).replace("\\", "/")
                            found = True
                            break
                if found:
                    break

            if not found:
                typer.echo(
                    f"Warning: Test '{test_name}' not found. Running specified module instead."
                )

    # Ensure all path separators are forward slashes for pytest
    test_path = test_path.replace("\\", "/")
    command.append(test_path)

    # Add options
    if verbose:
        command.append("-v")

    # Join command parts
    cmd_str = " ".join(command)

    # Run the command
    typer.echo(f"Running: {cmd_str}")
    return lib.run(cmd_str, cwd=_tests_cwd()).returncode != 0


_PYTEST_NO_TESTS_COLLECTED = 5


def _tests_cwd() -> str | None:
    """
    Directory to run pytest from: the source checkout, not the runtime home.

    `lib.run` defaults a subprocess to `MASCOPE_PATH`, which locates the
    shared runtime home - database volumes, secrets, `.runtime` - and normally
    points at an entirely different checkout from the one the CLI is running
    from. Tests must follow the running source, for the same reason
    `checkout.backend_path` gives for alembic: a worktree carries its own
    code, and collecting the main checkout's tests while importing the
    worktree's installed packages fails on import file mismatch, or worse,
    silently tests the wrong tree.

    :return: The checkout root, or None to leave the default in place.
    :rtype: str | None
    """
    root = source_checkout()
    return str(root) if root is not None else None


# A module carries a doctest only if its source contains a PS1 prompt. Matching
# the text is enough: a false positive costs one extra module import, while
# selecting whole `src` trees costs every module in them (see below).
_DOCTEST_PROMPT = ">>>"


def _library_doctest_paths(test_path: str, cwd: str | None = None) -> list[str]:
    """
    Modules to collect doctests from, for a library test path.

    Only the modules that actually carry one. Handing pytest the whole ``src``
    tree makes ``--doctest-modules`` import every module in it, and three of
    them import ``mascope_backend``, which reads the Postgres secret at import
    time - so a library run would abort on a checkout with no secrets even
    though the library suite itself passes there. Selecting by content keeps
    the pass proportional to the handful of doctests that exist and needs no
    ignore list to maintain.

    :param test_path: The pytest path the test pass was given.
    :type test_path: str
    :param cwd: Directory the paths are relative to, defaults to the caller's.
                The pytest subprocess runs from the source checkout, so
                discovery has to look there too rather than at wherever the
                CLI process happens to be standing.
    :type cwd: str | None, optional
    :return: Repo-relative paths of modules carrying a doctest, sorted.
    :rtype: list[str]
    """
    root = test_path.rstrip("/")
    if not root or root.endswith(".py"):
        return []
    base = cwd or os.getcwd()
    if not os.path.isdir(os.path.join(base, root)):
        return []
    candidates = (
        [f"{root}/{entry}" for entry in sorted(os.listdir(os.path.join(base, root)))]
        if root == "libraries"
        else [root]
    )

    found: list[str] = []
    for candidate in candidates:
        src = os.path.join(base, candidate, "src")
        if not os.path.isdir(src):
            continue
        for directory, _, filenames in os.walk(src):
            for filename in sorted(filenames):
                if not filename.endswith(".py"):
                    continue
                path = os.path.join(directory, filename)
                try:
                    with open(path, encoding="utf-8") as handle:
                        if _DOCTEST_PROMPT not in handle.read():
                            continue
                except OSError:
                    continue
                found.append(os.path.relpath(path, base).replace("\\", "/"))
    return sorted(found)


def run_library_tests(
    module: TestModule | None,
    test_name: str | None,
    verbose: bool,
) -> bool:
    """Run library tests with the specified options.

    :return: True when the suite or the doctest pass failed.
    :rtype: bool
    """
    # Base command
    command = ["pytest"]

    # Path to tests - always use forward slashes for paths
    test_path = "libraries/"

    # Handle module selection using the enum value
    if module and module != TestModule.ALL:
        if module == TestModule.MASCOPE_SDK:
            test_path = f"{test_path}sdk/"
        elif module == TestModule.MASCOPE_TOOLS:
            test_path = f"{test_path}tools/"
        elif module == TestModule.MASCOPE_FILE:
            test_path = f"{test_path}file/"
        elif module == TestModule.MASCOPE_MATCH:
            test_path = f"{test_path}match/"
        elif module == TestModule.MASCOPE_SIGNAL:
            test_path = f"{test_path}signal/"
        elif module == TestModule.MASCOPE_THERMO:
            test_path = f"{test_path}thermo/"
        else:
            typer.echo(
                "Warning: Library tests are not separated into unit/integration/system modules. "
                "Running all library tests instead."
            )

    # If a specific test name is provided, search for it
    if test_name:
        # Check if it's a full path
        if "/" in test_name or "\\" in test_name:
            # Normalize path separators to forward slashes
            test_name = test_name.replace("\\", "/")
            test_path = f"{test_path}{test_name}"
            if not test_path.endswith(".py"):
                test_path += ".py"
        else:
            # Search for the test file
            found = False
            for root, _, files in os.walk("libraries"):
                for file in files:
                    if file.startswith("test_") and file.endswith(".py"):
                        # Extract test name without prefix and extension
                        name_match = file[5:-3]  # Strip "test_" and ".py"
                        if name_match == test_name:
                            # Found the test file - normalize to forward slashes
                            test_path = os.path.join(root, file).replace("\\", "/")
                            found = True
                            break
                if found:
                    break

            if not found:
                typer.echo(f"Warning: Test '{test_name}' not found. Exiting...")
                return False

    # Ensure all path separators are forward slashes for pytest
    test_path = test_path.replace("\\", "/")
    command.append(test_path)

    # Add options
    if verbose:
        command.append("-v")

    # Join command parts
    cmd_str = " ".join(command)

    # Run the command
    typer.echo(f"Running: {cmd_str}")
    failed = lib.run(cmd_str, cwd=_tests_cwd()).returncode != 0

    # Doctests, as a second pass over the modules that carry one.
    #
    # They used to ride along as --doctest-modules on the run above, which
    # collects the tests directories too. Six of the ten libraries ship a
    # tests/conftest.py, and under pytest's default import mode the second one
    # collected is an "import file mismatch" - so the whole run aborted with a
    # collection error and no doctest had run for as long as that was true.
    # Source trees carry no conftest.py, so collecting them alone has nothing
    # to collide with, and running them separately leaves the test pass on the
    # import mode its own tests rely on (several import their sibling conftest
    # as a top-level module, which --import-mode=importlib forbids).
    if not test_name:
        doctest_paths = _library_doctest_paths(test_path, cwd=_tests_cwd())
        if doctest_paths:
            doctest_command = ["pytest", *doctest_paths, "--doctest-modules"]
            if verbose:
                doctest_command.append("-v")
            doctest_cmd_str = " ".join(doctest_command)
            typer.echo(f"Running doctests: {doctest_cmd_str}")
            # 5 is pytest's "no tests collected". A library that carries no
            # doctests is not a failure, so only a real one counts here.
            doctest_code = lib.run(doctest_cmd_str, cwd=_tests_cwd()).returncode
            failed = doctest_code not in (0, _PYTEST_NO_TESTS_COLLECTED) or failed

    # Returned rather than raised: the caller runs the other components and
    # fails once at the end. The command used to report success whatever
    # pytest answered.
    return failed


@test_app.command()
def show():
    """Show available test modules and test files

    This command scans the tests directory and shows all available test modules
    organized by their location. Each test is displayed with its name and path
    to make it easy to run specific tests with the 'run -n' command.
    """
    backend_tests_dir = "server/backend/tests"
    libraries_root_dir = "libraries"
    sdk_tests_dir = os.path.join(libraries_root_dir, "sdk", "tests")
    tools_tests_dir = os.path.join(libraries_root_dir, "tools", "tests")
    file_tests_dir = os.path.join(libraries_root_dir, "file", "tests")
    match_tests_dir = os.path.join(libraries_root_dir, "match", "tests")
    signal_tests_dir = os.path.join(libraries_root_dir, "signal", "tests")
    thermo_tests_dir = os.path.join(libraries_root_dir, "thermo", "tests")

    # ----- Backend tests -----
    if os.path.exists(backend_tests_dir):
        # Show module types using the enum
        for module in [m.value for m in TestModule if m != TestModule.ALL]:
            module_path = os.path.join(backend_tests_dir, module)

            if os.path.exists(module_path) and os.path.isdir(module_path):
                # Get count of test files in this module
                test_count = 0
                for root, _, files in os.walk(module_path):
                    test_count += sum(
                        1 for f in files if f.startswith("test_") and f.endswith(".py")
                    )

                typer.echo(f"\nbackend/{module}/ ({test_count} test files)")

                # Show subdirectories with their tests
                for item in sorted(os.listdir(module_path)):
                    item_path = os.path.join(module_path, item)

                    if os.path.isdir(item_path) and not item.startswith("__"):
                        # Count tests in this subdir
                        subdir_tests = []
                        for root, _, files in os.walk(item_path):
                            for file in sorted(
                                f
                                for f in files
                                if f.startswith("test_") and f.endswith(".py")
                            ):
                                # Get relative path from module directory
                                rel_path = os.path.relpath(
                                    os.path.join(root, file), module_path
                                )
                                rel_path = rel_path.replace("\\", "/")
                                test_name = file[5:-3]
                                subdir_tests.append((rel_path, test_name))

                        if subdir_tests:
                            typer.echo(f"  {item}/ ({len(subdir_tests)} test files)")
                            for rel_path, test_name in subdir_tests:
                                typer.echo(f"    - {test_name} → {rel_path}")

                # Check for test files directly in the module directory
                module_files = []
                for file in sorted(os.listdir(module_path)):
                    if file.startswith("test_") and file.endswith(".py"):
                        test_name = file[5:-3]
                        module_files.append((file, test_name))

                if module_files:
                    typer.echo(f"  (root) ({len(module_files)} test files)")
                    for file, test_name in module_files:
                        typer.echo(f"    - {test_name} → {file}")

        # Check for root test files
        root_tests = []
        for file in sorted(os.listdir(backend_tests_dir)):
            if file.startswith("test_") and file.endswith(".py"):
                test_name = file[5:-3]
                root_tests.append((file, test_name))

        if root_tests:
            typer.echo(f"\nbackend/root/ ({len(root_tests)} tests)")
            for file, test_name in root_tests:
                typer.echo(f"  - {test_name} → {file}")
    else:
        typer.echo(f"\nBackend tests directory not found at {backend_tests_dir}")

    # ----- Library tests (sdk/tools) -----
    def _show_library_section(title: str, base_dir: str):
        if not os.path.exists(base_dir):
            typer.echo(f"\n{title} tests directory not found at {base_dir}")
            return

        # Count all test_*.py files under this base_dir
        total = 0
        for root, _, files in os.walk(base_dir):
            total += sum(
                1 for f in files if f.startswith("test_") and f.endswith(".py")
            )

        typer.echo(f"\n{title}/ ({total} test files)")
        # Show subdirectories and files
        entries = []
        for root, _, files in os.walk(base_dir):
            for f in sorted(files):
                if f.startswith("test_") and f.endswith(".py"):
                    rel = os.path.relpath(os.path.join(root, f), base_dir).replace(
                        "\\", "/"
                    )
                    name = f[5:-3]
                    entries.append((rel, name))

        if entries:
            for rel, name in sorted(entries):
                typer.echo(f"  - {name} → {rel}")
        else:
            typer.echo("  (no tests found)")

    _show_library_section("libraries/sdk/tests", sdk_tests_dir)
    _show_library_section("libraries/tools/tests", tools_tests_dir)
    _show_library_section("libraries/file/tests", file_tests_dir)
    _show_library_section("libraries/match/tests", match_tests_dir)
    _show_library_section("libraries/signal/tests", signal_tests_dir)
    _show_library_section("libraries/thermo/tests", thermo_tests_dir)

    typer.echo("\nUsage examples:")
    typer.echo("  mascope test show                      # Show available tests")
    typer.echo("  mascope test run                       # Run all tests")
    typer.echo("  mascope test run -m unit               # Run only backend unit tests")
    typer.echo("  mascope test run libraries -m sdk      # Run SDK library tests")
    typer.echo("  mascope test run libraries -m tools    # Run Tools library tests")
    typer.echo("  mascope test run libraries -m file     # Run File library tests")
    typer.echo("  mascope test run libraries -m match    # Run Match library tests")
    typer.echo("  mascope test run libraries -m signal   # Run Signal library tests")
    typer.echo("  mascope test run -n dataset_model      # Run specific test by name")
    typer.echo("  mascope test run -m migrations         # Run Alembic migration tests")

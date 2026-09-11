import os
from pathlib import Path
from typing import Annotated

import typer


backend_app = typer.Typer()


@backend_app.callback()
def main():
    """
    Run services in the Mascope backend
    """
    pass


services = ["api-server", "file-converter"]


@backend_app.command()
def run(
    service: Annotated[
        str,
        typer.Argument(
            help="The backend service to launch, one of: " + ", ".join(services),
        ),
    ] = "api-server",
):
    """
    Launch a Mascope backend service
    """
    match service:
        case "api-server":
            from mascope_backend.app import run as run_api_server

            run_api_server()
        case "file-converter":
            from mascope_backend.file_converter.service import run as run_file_converter

            run_file_converter()
        case _:
            raise ValueError(f"Unknown service: {service}")


@backend_app.command()
def openapi(
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="File to write the document to (JSON)."),
    ],
):
    """
    Render the OpenAPI document production deployments publish

    The backend serves its schema in dev mode only, so a deployment publishes a
    static copy at /docs/openapi.json, rendered with this command when the
    frontend image is built. The render runs in a throwaway prod runtime with
    placeholder secrets and takes only the config layers from MASCOPE_PATH, so
    the document comes out the same on any machine.
    """
    from mascope_backend.openapi import OpenApiRenderError, render

    mascope_path = os.environ.get("MASCOPE_PATH")
    if not mascope_path:
        typer.echo(
            "MASCOPE_PATH is not set; the config layers are read from it", err=True
        )
        raise typer.Exit(1)
    try:
        document = render(output, Path(mascope_path))
    except OpenApiRenderError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    typer.echo(f"Wrote {output} ({len(document['paths'])} paths)")


def exec():
    backend_app()

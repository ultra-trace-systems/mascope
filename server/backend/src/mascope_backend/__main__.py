"""
``python -m mascope_backend``: the ``mascope-backend`` CLI, without its script.

The console script is declared by the root ``mascope`` project, so an
environment holding only this package - ``uv sync --package mascope_backend``,
as the frontend image's OpenAPI stage and the docs CI job install it - has no
``mascope-backend`` to call.
"""

from mascope_backend.main import backend_app


backend_app(prog_name="mascope-backend")

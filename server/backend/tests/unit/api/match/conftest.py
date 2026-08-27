"""Shared stubs for the ``rematch_batches`` unit tests.

Several test modules drive ``rematch_batches`` with its dependencies mocked.
They all have to stand in for the one query it runs itself - the grouped
``COUNT`` that sizes each batch's share of the progress bar - so that stub
lives here rather than being copied into each of them.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock


@asynccontextmanager
async def counting_session(counts: dict[str, int] | None = None):
    """Stand in for ``async_session`` during the sample-count weighting query.

    Patch it in as ``patch(f"{_CTRL}.async_session", counting_session)``.

    ``rematch_batches`` used to call ``get_samples`` once per batch purely to
    count rows, and tests stubbed that; it now asks for one grouped count
    instead, so the seam moved with it.

    Reporting no rows (the default) is usually what a test wants: the counts
    only size each batch's share of the progress bar, and with none the
    aggregate falls back to equal shares per batch. Pass ``counts`` only when
    the test actually asserts on progress.

    :param counts: Sample count per batch id, or None for no rows.
    :type counts: dict[str, int] | None
    """
    session = AsyncMock()
    result = AsyncMock()
    rows = list((counts or {}).items())
    result.all = lambda: rows
    session.execute.return_value = result
    yield session

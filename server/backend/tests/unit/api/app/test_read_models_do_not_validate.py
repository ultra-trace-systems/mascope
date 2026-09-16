"""Tests: a response model reports what is stored, it does not re-validate it.

A ``*Read`` model that carries the write validators turns one row written
under older rules - or under a rule that has since tightened - into an error
for the whole listing it appears in, for every caller. The write models are
where what may be stored is decided; the read models spell the fields only.
"""

import inspect
import sys

from pydantic import BaseModel

# Imported for the side effect: registering every router pulls in every
# request and response model, so the scan below sees all of them.
import mascope_backend.app.fast  # noqa: F401


def _read_models():
    """Every ``*Read`` pydantic model the app has imported."""
    models = {}
    for name, module in list(sys.modules.items()):
        if not name.startswith("mascope_backend."):
            continue
        for obj in vars(module).values():
            if (
                inspect.isclass(obj)
                and issubclass(obj, BaseModel)
                and obj.__name__.endswith("Read")
            ):
                models[f"{obj.__module__}.{obj.__name__}"] = obj
    return models


def test_no_read_model_carries_validators():
    models = _read_models()
    # Guards the scan itself: an import that stops reaching the models would
    # otherwise make this pass over nothing.
    assert len(models) >= 8, sorted(models)

    with_validators = {}
    for name, model in sorted(models.items()):
        decorators = model.__pydantic_decorators__
        validators = sorted(
            [*decorators.field_validators, *decorators.model_validators]
        )
        if validators:
            with_validators[name] = validators
    assert with_validators == {}

"""
Tests for the config compatibility shim and the upload settings it moves.

`tus_max_upload_gb` moved from `[backend]` to `[meta]` so the frontend, which
only ever sees `[meta]`, can size the browser's upload limit from the same
value the backend enforces. The models ignore unknown keys, so without
`migrate_legacy_options` an operator's raised cap would silently revert to the
default on upgrade — exactly on the deployments that raised it because their
instrument writes large single files.
"""

import pytest
from pydantic import ValidationError

from mascope_runtime.config import BackendConfig, MetaConfig, migrate_legacy_options


class _RecordingLogger:
    """Collects the warnings the migration emits."""

    def __init__(self):
        self.warnings = []

    def warning(self, message):
        self.warnings.append(message)


def test_legacy_backend_tus_cap_is_promoted_to_meta():
    logger = _RecordingLogger()
    raw = migrate_legacy_options(
        {"meta": {}, "backend": {"tus_max_upload_gb": 20}}, logger
    )
    assert raw["meta"]["tus_max_upload_gb"] == 20
    assert "tus_max_upload_gb" not in raw["backend"]
    assert len(logger.warnings) == 1
    assert "20" in logger.warnings[0]


def test_meta_wins_when_both_sections_set_the_cap():
    logger = _RecordingLogger()
    raw = migrate_legacy_options(
        {"meta": {"tus_max_upload_gb": 8}, "backend": {"tus_max_upload_gb": 20}},
        logger,
    )
    assert raw["meta"]["tus_max_upload_gb"] == 8
    assert "tus_max_upload_gb" not in raw["backend"]
    assert len(logger.warnings) == 1


def test_no_legacy_key_is_a_noop():
    logger = _RecordingLogger()
    raw = migrate_legacy_options({"meta": {"api_port": 8090}, "backend": {}}, logger)
    assert raw == {"meta": {"api_port": 8090}, "backend": {}}
    assert logger.warnings == []


def test_missing_meta_section_is_created():
    """Defensive: [meta] always exists in practice, but the shim must not
    depend on it — a raw dict is merged from three toml layers, any of which
    may be absent."""
    raw = migrate_legacy_options({"backend": {"tus_max_upload_gb": 7}})
    assert raw == {"meta": {"tus_max_upload_gb": 7}, "backend": {}}


def test_migration_survives_a_missing_backend_section():
    assert migrate_legacy_options({"meta": {}}) == {"meta": {}}


def test_meta_config_carries_the_upload_cap():
    """The cap and its lower bound survived the move out of BackendConfig."""
    assert MetaConfig().tus_max_upload_gb == 5
    assert MetaConfig(tus_max_upload_gb=20).tus_max_upload_gb == 20
    with pytest.raises(ValidationError):
        MetaConfig(tus_max_upload_gb=0)


def test_backend_config_disk_floor_default():
    """0 is the documented "disabled" value; a negative floor is not."""
    assert BackendConfig(name="backend").tus_min_free_disk_gb == 10
    assert (
        BackendConfig(name="backend", tus_min_free_disk_gb=0).tus_min_free_disk_gb == 0
    )
    with pytest.raises(ValidationError):
        BackendConfig(name="backend", tus_min_free_disk_gb=-1)

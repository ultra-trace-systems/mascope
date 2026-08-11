"""Add peak assignment tables

Introduces the peak-centric assignment persistence layer in one revision:

- peak_assignment_run: one row per assignment run over a sample, storing the
  engine version and full configuration for reproducibility.
- peak_assignment: one row per observed sample peak in a run, carrying the
  committed formula, adduct, evidence, confidence tier, and optional references
  back to the curated target library. Peak identity follows the MatchIsotope
  pattern (sample_item_id FK + sample_peak_id string, with denormalized
  mz/intensity/tof); raw peaks stay in files.
- assignment_calibration: the per-instrument confidence calibration store (Platt
  curve + per-adduct corroboration weights), read by the engine to report a
  calibrated P(correct).
- assignment_verification: user confirm/reject verdicts on an assignment, with
  the score snapshot they were judged against - the labels a future
  recalibration is fit on.

The unique constraint on (peak_assignment_run_id, sample_peak_id) enforces the
single-owner-per-peak invariant within a run.

Squashed from four revisions that only ever existed on the peak-centric branch
(a1f8c25d9e47, b2e9d7c14a05, d1a2c3b4e5f6, e4f2a7c9d3b1). One of them renamed
peak_assignment.match_score to fit_score on a table the branch itself had created
two revisions earlier, so every deployment would have created the column and
immediately renamed it; the column is simply named fit_score here. The reference
tables stay in their own revision - they are an independent subsystem with no
foreign key in either direction.

Revision ID: a1f8c25d9e47
Revises: c4f7a2e9b1d8
Create Date: 2026-07-04 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a1f8c25d9e47"
down_revision: Union[str, Sequence[str], None] = "c4f7a2e9b1d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "peak_assignment_run",
        sa.Column("peak_assignment_run_id", sa.String(length=16), nullable=False),
        sa.Column("sample_item_id", sa.String(length=16), nullable=False),
        sa.Column("engine_version", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "peak_assignment_run_utc_created",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "peak_assignment_run_utc_completed",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["sample_item_id"],
            ["sample_item.sample_item_id"],
            name=op.f("fk_peak_assignment_run_sample_item_id_sample_item"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "peak_assignment_run_id", name=op.f("pk_peak_assignment_run")
        ),
    )
    op.create_index(
        op.f("ix_peak_assignment_run_sample_item_id"),
        "peak_assignment_run",
        ["sample_item_id"],
        unique=False,
    )
    op.create_table(
        "peak_assignment",
        sa.Column("peak_assignment_id", sa.String(length=32), nullable=False),
        sa.Column("peak_assignment_run_id", sa.String(length=16), nullable=False),
        sa.Column("sample_item_id", sa.String(length=16), nullable=False),
        sa.Column("sample_peak_id", sa.String(length=20), nullable=False),
        sa.Column("sample_peak_mz", sa.Float(), nullable=False),
        sa.Column("sample_peak_intensity", sa.Float(), nullable=False),
        sa.Column("sample_peak_tof", sa.Float(), nullable=True),
        sa.Column(
            "role",
            sa.String(length=16),
            server_default=sa.text("'unassigned'"),
            nullable=False,
        ),
        sa.Column("assigned_formula", sa.String(length=256), nullable=True),
        sa.Column("ion_formula", sa.String(length=4096), nullable=True),
        sa.Column("ionization_mechanism_id", sa.String(length=16), nullable=True),
        sa.Column("isotope_label", sa.String(length=64), nullable=True),
        sa.Column("isotope_formula", sa.String(length=256), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=True),
        sa.Column("fit_score", sa.Float(), nullable=True),
        sa.Column("mz_error_ppm", sa.Float(), nullable=True),
        sa.Column("abundance_error", sa.Float(), nullable=True),
        sa.Column(
            "tier",
            sa.String(length=24),
            server_default=sa.text("'unassigned'"),
            nullable=False,
        ),
        sa.Column("target_compound_id", sa.String(length=16), nullable=True),
        sa.Column("target_ion_id", sa.String(length=16), nullable=True),
        sa.Column("owner_peak_assignment_id", sa.String(length=32), nullable=True),
        sa.Column("alternatives", sa.JSON(), nullable=True),
        sa.Column("provenance", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "fit_score IS NULL OR fit_score BETWEEN 0 AND 1",
            name=op.f("ck_peak_assignment_fit_score_range"),
        ),
        sa.ForeignKeyConstraint(
            ["ionization_mechanism_id"],
            ["ionization_mechanism.ionization_mechanism_id"],
            name=op.f(
                "fk_peak_assignment_ionization_mechanism_id_ionization_mechanism"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["owner_peak_assignment_id"],
            ["peak_assignment.peak_assignment_id"],
            name=op.f("fk_peak_assignment_owner_peak_assignment_id_peak_assignment"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["peak_assignment_run_id"],
            ["peak_assignment_run.peak_assignment_run_id"],
            name=op.f("fk_peak_assignment_peak_assignment_run_id_peak_assignment_run"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sample_item_id"],
            ["sample_item.sample_item_id"],
            name=op.f("fk_peak_assignment_sample_item_id_sample_item"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_compound_id"],
            ["target_compound.target_compound_id"],
            name=op.f("fk_peak_assignment_target_compound_id_target_compound"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["target_ion_id"],
            ["target_ion.target_ion_id"],
            name=op.f("fk_peak_assignment_target_ion_id_target_ion"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("peak_assignment_id", name=op.f("pk_peak_assignment")),
        sa.UniqueConstraint(
            "peak_assignment_run_id",
            "sample_peak_id",
            name="uq_peak_assignment_run_id_sample_peak_id",
        ),
    )
    op.create_index(
        op.f("ix_peak_assignment_peak_assignment_run_id"),
        "peak_assignment",
        ["peak_assignment_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_peak_assignment_sample_item_id"),
        "peak_assignment",
        ["sample_item_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_peak_assignment_sample_peak_id"),
        "peak_assignment",
        ["sample_peak_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_peak_assignment_target_compound_id"),
        "peak_assignment",
        ["target_compound_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_peak_assignment_target_ion_id"),
        "peak_assignment",
        ["target_ion_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_peak_assignment_ionization_mechanism_id"),
        "peak_assignment",
        ["ionization_mechanism_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_peak_assignment_owner_peak_assignment_id"),
        "peak_assignment",
        ["owner_peak_assignment_id"],
        unique=False,
    )
    op.create_table(
        "assignment_calibration",
        sa.Column(
            "assignment_calibration_id",
            sa.Integer(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("instrument", sa.String(length=32), nullable=False),
        sa.Column("score_version", sa.Integer(), nullable=False),
        sa.Column("a", sa.Float(), nullable=False),
        sa.Column("b", sa.Float(), nullable=False),
        sa.Column("n_pos", sa.Integer(), nullable=False),
        sa.Column("n_neg", sa.Integer(), nullable=False),
        sa.Column("ece", sa.Float(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("provisional", sa.Boolean(), nullable=False),
        sa.Column("corroboration_weights", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("fit_utc", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_utc", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "assignment_calibration_id", name=op.f("pk_assignment_calibration")
        ),
    )
    op.create_index(
        op.f("ix_assignment_calibration_instrument"),
        "assignment_calibration",
        ["instrument"],
        unique=False,
    )
    op.create_index(
        op.f("ix_assignment_calibration_score_version"),
        "assignment_calibration",
        ["score_version"],
        unique=False,
    )
    op.create_index(
        op.f("ix_assignment_calibration_is_active"),
        "assignment_calibration",
        ["is_active"],
        unique=False,
    )
    op.create_index(
        "ix_assignment_calibration_active",
        "assignment_calibration",
        ["instrument", "score_version", "is_active"],
        unique=False,
    )
    op.create_table(
        "assignment_verification",
        sa.Column("assignment_verification_id", sa.String(length=32), nullable=False),
        sa.Column("sample_item_id", sa.String(length=16), nullable=False),
        sa.Column("peak_assignment_id", sa.String(length=32), nullable=True),
        sa.Column("peak_assignment_run_id", sa.String(length=16), nullable=True),
        sa.Column("sample_peak_id", sa.String(length=20), nullable=False),
        sa.Column("assigned_formula", sa.String(length=256), nullable=True),
        sa.Column("ionization_mechanism_id", sa.String(length=16), nullable=True),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("evidence_level", sa.String(length=24), nullable=True),
        sa.Column("fit_score", sa.Float(), nullable=True),
        sa.Column("evidence", sa.Float(), nullable=True),
        sa.Column("p_correct", sa.Float(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("context", sa.JSON(), nullable=True),
        sa.Column("verified_by", sa.Integer(), nullable=True),
        sa.Column("verified_utc", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "verdict IN ('confirmed', 'rejected', 'unsure')",
            name=op.f("ck_assignment_verification_verdict_valid"),
        ),
        sa.CheckConstraint(
            "evidence_level IS NULL OR evidence_level IN "
            "('reference_standard', 'msms', 'orthogonal', 'pattern', 'visual')",
            name=op.f("ck_assignment_verification_evidence_level_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["sample_item_id"],
            ["sample_item.sample_item_id"],
            name=op.f("fk_assignment_verification_sample_item_id_sample_item"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["peak_assignment_id"],
            ["peak_assignment.peak_assignment_id"],
            name=op.f("fk_assignment_verification_peak_assignment_id_peak_assignment"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["verified_by"],
            ["user.id"],
            name=op.f("fk_assignment_verification_verified_by_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "assignment_verification_id",
            name=op.f("pk_assignment_verification"),
        ),
    )
    op.create_index(
        op.f("ix_assignment_verification_sample_item_id"),
        "assignment_verification",
        ["sample_item_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_assignment_verification_peak_assignment_id"),
        "assignment_verification",
        ["peak_assignment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_assignment_verification_sample_peak_id"),
        "assignment_verification",
        ["sample_peak_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_assignment_verification_verified_by"),
        "assignment_verification",
        ["verified_by"],
        unique=False,
    )
    op.create_index(
        "ix_assignment_verification_identity",
        "assignment_verification",
        ["sample_item_id", "sample_peak_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_assignment_verification_identity",
        table_name="assignment_verification",
    )
    op.drop_index(
        op.f("ix_assignment_verification_verified_by"),
        table_name="assignment_verification",
    )
    op.drop_index(
        op.f("ix_assignment_verification_sample_peak_id"),
        table_name="assignment_verification",
    )
    op.drop_index(
        op.f("ix_assignment_verification_peak_assignment_id"),
        table_name="assignment_verification",
    )
    op.drop_index(
        op.f("ix_assignment_verification_sample_item_id"),
        table_name="assignment_verification",
    )
    op.drop_table("assignment_verification")
    op.drop_index(
        "ix_assignment_calibration_active", table_name="assignment_calibration"
    )
    op.drop_index(
        op.f("ix_assignment_calibration_is_active"),
        table_name="assignment_calibration",
    )
    op.drop_index(
        op.f("ix_assignment_calibration_score_version"),
        table_name="assignment_calibration",
    )
    op.drop_index(
        op.f("ix_assignment_calibration_instrument"),
        table_name="assignment_calibration",
    )
    op.drop_table("assignment_calibration")
    op.drop_index(
        op.f("ix_peak_assignment_owner_peak_assignment_id"),
        table_name="peak_assignment",
    )
    op.drop_index(
        op.f("ix_peak_assignment_ionization_mechanism_id"),
        table_name="peak_assignment",
    )
    op.drop_index(
        op.f("ix_peak_assignment_target_ion_id"), table_name="peak_assignment"
    )
    op.drop_index(
        op.f("ix_peak_assignment_target_compound_id"), table_name="peak_assignment"
    )
    op.drop_index(
        op.f("ix_peak_assignment_sample_peak_id"), table_name="peak_assignment"
    )
    op.drop_index(
        op.f("ix_peak_assignment_sample_item_id"), table_name="peak_assignment"
    )
    op.drop_index(
        op.f("ix_peak_assignment_peak_assignment_run_id"),
        table_name="peak_assignment",
    )
    op.drop_table("peak_assignment")
    op.drop_index(
        op.f("ix_peak_assignment_run_sample_item_id"),
        table_name="peak_assignment_run",
    )
    op.drop_table("peak_assignment_run")

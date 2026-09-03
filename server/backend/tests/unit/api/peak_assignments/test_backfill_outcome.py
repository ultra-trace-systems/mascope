"""What a batch-peak backfill tells the user about what it did.

The backfill is offered as a button in the batch-peak ledger, and the state it
exists to repair - a batch whose samples were assigned before batch peaks
existed - is indistinguishable from the state it cannot repair: a batch with no
completed assignment runs at all. Both accept the request; only one does
anything. Announcing the second as a success is how a user arrives back at an
empty ledger having been told the peaks were computed.

A fold that raises is a third outcome again, and the one where a wrong message
does real harm: "assign the batch first" is advice that cannot help, and it
sends the user off to re-run an assignment instead of reading the log.
"""

from mascope_backend.api.lib.api_features import (
    RESULT_STATUS_NOTIFICATION,
    notification_status,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    backfill_outcome,
)


def test_folding_samples_is_a_success_naming_the_count():
    result = backfill_outcome(3, 0, "batch-1")

    assert notification_status(result) == "success"
    assert "3 sample(s)" in result["message"]
    assert result["data"] == {"samples_folded": 3, "samples_failed": 0}


def test_folding_nothing_is_announced_as_a_warning():
    result = backfill_outcome(0, 0, "batch-1")

    assert notification_status(result) == "warning"
    assert result["data"] == {"samples_folded": 0, "samples_failed": 0}


def test_folding_nothing_says_what_to_do_about_it():
    # The message is the whole user-facing outcome - there is no other surface
    # that reports a background task's result - so it has to name the cause.
    message = backfill_outcome(0, 0, "batch-1")["message"]

    assert "could be folded" in message
    assert "blank" in message


def test_every_fold_failing_is_an_error_not_advice_to_assign_the_batch():
    # Same zero count, opposite cause: the samples were assigned and the folds
    # raised. Telling this user to assign the batch first would send them to
    # re-run an assignment that is already there.
    result = backfill_outcome(0, 4, "batch-1")

    assert notification_status(result) == "error"
    assert "4 sample(s) failed" in result["message"]
    assert "could be folded" not in result["message"]
    assert result["data"] == {"samples_folded": 0, "samples_failed": 4}


def test_a_partly_failed_fold_is_a_warning_naming_both_counts():
    # Reporting only what succeeded reads as a complete result, and the ledger
    # is then quietly missing a sample.
    result = backfill_outcome(4, 1, "batch-1")

    assert notification_status(result) == "warning"
    assert "4 sample(s)" in result["message"]
    assert "1 sample(s) failed" in result["message"]
    assert result["data"] == {"samples_folded": 4, "samples_failed": 1}


def test_every_outcome_carries_the_batch_the_notification_is_routed_by():
    # `_notification_data` is what the decorator puts on the packet, and the
    # room it is emitted to is read from the same key.
    for folded, failed in ((0, 0), (0, 2), (2, 1), (2, 0)):
        assert backfill_outcome(folded, failed, "batch-1")["_notification_data"] == {
            "sample_batch_id": "batch-1"
        }


def test_the_statuses_used_are_ones_the_notification_layer_translates():
    # An outcome outside this map is announced green with only a log line to
    # say so, which is the failure this whole test module is about.
    for folded, failed in ((0, 0), (0, 2), (2, 1), (2, 0)):
        assert (
            backfill_outcome(folded, failed, "batch-1")["status"]
            in RESULT_STATUS_NOTIFICATION
        )

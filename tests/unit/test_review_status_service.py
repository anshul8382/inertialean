"""Unit tests for review status lifecycle."""
from types import SimpleNamespace

import pytest

from services.review_status_service import (
    STATUS_CLOSED,
    STATUS_DEFERRED,
    STATUS_DUE,
    STATUS_INITIATED,
    STATUS_MEETING,
    STATUS_PLANNED,
    STATUS_SENT,
    SYSTEM_SETTABLE,
    ReviewStatusError,
    allowed_status_values,
    apply_user_status,
    system_set_due,
    user_selectable_choices,
)

pytestmark = pytest.mark.no_app


def test_system_may_only_set_due():
    assert SYSTEM_SETTABLE == frozenset({STATUS_DUE})


def test_user_can_set_due():
    wf = SimpleNamespace(status=STATUS_PLANNED)
    assert apply_user_status(wf, STATUS_DUE) == STATUS_DUE
    assert wf.status == STATUS_DUE


def test_due_to_planned_deferred_initiated():
    for target in (STATUS_PLANNED, STATUS_DEFERRED, STATUS_INITIATED, STATUS_CLOSED):
        wf = SimpleNamespace(status=STATUS_DUE)
        assert apply_user_status(wf, target) == target
        assert wf.status == target


def test_planned_to_initiated():
    wf = SimpleNamespace(status=STATUS_PLANNED)
    apply_user_status(wf, STATUS_INITIATED)
    assert wf.status == STATUS_INITIATED


def test_deferred_to_planned():
    wf = SimpleNamespace(status=STATUS_DEFERRED)
    apply_user_status(wf, STATUS_PLANNED)
    assert wf.status == STATUS_PLANNED


def test_initiated_can_triage_to_due_planned_or_deferred():
    for target in (STATUS_DUE, STATUS_PLANNED, STATUS_DEFERRED):
        wf = SimpleNamespace(status=STATUS_INITIATED)
        assert apply_user_status(wf, target) == target
        assert wf.status == target


def test_illegal_jump_blocked():
    wf = SimpleNamespace(status=STATUS_PLANNED)
    with pytest.raises(ReviewStatusError):
        apply_user_status(wf, STATUS_SENT)


def test_system_set_due():
    wf = SimpleNamespace(status=STATUS_INITIATED)
    assert system_set_due(wf) == STATUS_DUE
    assert wf.status == STATUS_DUE


def test_user_choices_include_due_and_all_statuses():
    for current in (
        STATUS_DUE,
        STATUS_PLANNED,
        STATUS_DEFERRED,
        STATUS_INITIATED,
        STATUS_SENT,
        STATUS_MEETING,
        STATUS_CLOSED,
    ):
        values = [v for v, _ in user_selectable_choices(current)]
        assert values[0] == STATUS_DUE
        assert STATUS_PLANNED in values
        assert STATUS_DEFERRED in values
        assert STATUS_INITIATED in values
        assert STATUS_SENT in values
        assert STATUS_MEETING in values
        assert STATUS_CLOSED in values

    allowed = allowed_status_values(STATUS_PLANNED)
    assert STATUS_DUE in allowed
    assert STATUS_PLANNED in allowed
    assert STATUS_INITIATED in allowed
    assert STATUS_SENT not in allowed

"""Unit tests for review status lifecycle."""
from types import SimpleNamespace

import pytest

from services.review_status_service import (
    STATUS_CLOSED,
    STATUS_DEFERRED,
    STATUS_DUE,
    STATUS_INITIATED,
    STATUS_PLANNED,
    STATUS_SENT,
    ReviewStatusError,
    apply_user_status,
    system_set_due,
    user_selectable_choices,
)

pytestmark = pytest.mark.no_app


def test_user_cannot_set_due():
    wf = SimpleNamespace(status=STATUS_PLANNED)
    with pytest.raises(ReviewStatusError, match="due"):
        apply_user_status(wf, STATUS_DUE)


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


def test_illegal_jump_blocked():
    wf = SimpleNamespace(status=STATUS_INITIATED)
    with pytest.raises(ReviewStatusError):
        apply_user_status(wf, STATUS_PLANNED)


def test_system_set_due():
    wf = SimpleNamespace(status=STATUS_INITIATED)
    assert system_set_due(wf) == STATUS_DUE
    assert wf.status == STATUS_DUE


def test_user_choices_exclude_due_when_current_due():
    choices = user_selectable_choices(STATUS_DUE)
    values = [v for v, _ in choices]
    assert STATUS_DUE not in values
    assert STATUS_PLANNED in values
    assert STATUS_DEFERRED in values
    assert STATUS_INITIATED in values


def test_user_choices_include_current_when_planned():
    choices = user_selectable_choices(STATUS_PLANNED)
    values = [v for v, _ in choices]
    assert STATUS_PLANNED in values
    assert STATUS_DUE not in values
    assert STATUS_SENT not in values  # cannot jump planned → sent

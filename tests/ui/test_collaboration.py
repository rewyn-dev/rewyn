"""Comments, saved views and incident ownership (UI spec §36, §45)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rewyn.storage.local import LocalStore
from rewyn.ui import schemas as s
from rewyn.ui.collaboration import CollabStore, parse_subject, subject_href
from rewyn.ui.index import RunIndex


@pytest.fixture
def collab(store: LocalStore) -> CollabStore:
    return CollabStore(store)


def test_a_comment_is_addressed_to_something_the_console_can_open(collab: CollabStore):
    comment = collab.add_comment(
        s.CommentRequest(subject="run:run_1", body="this is the failing one"), author="raj"
    )
    assert comment.author == "raj"
    assert collab.comments("run:run_1") == [comment]
    assert collab.comments("run:other") == []
    assert subject_href("run:run_1") == "/runs/run_1"


def test_a_comment_on_something_unopenable_is_refused(collab: CollabStore):
    with pytest.raises(ValueError, match="not something the console can hold"):
        parse_subject("spreadsheet:q3")
    with pytest.raises(ValueError, match="not something the console can hold"):
        collab.add_comment(s.CommentRequest(subject="spreadsheet:q3", body="hi"), author="raj")


def test_an_empty_comment_is_refused(collab: CollabStore):
    with pytest.raises(ValueError, match="needs something in it"):
        collab.add_comment(s.CommentRequest(subject="run:run_1", body="   "), author="raj")


def test_a_comment_can_be_resolved_and_stays_in_the_thread(collab: CollabStore):
    comment = collab.add_comment(
        s.CommentRequest(subject="incident:errors-1", body="fixed by v20"), author="raj"
    )
    resolved = collab.resolve_comment(comment.id)
    assert resolved.resolved
    assert collab.comments("incident:errors-1")[0].resolved


def test_a_saved_view_keeps_the_filter_that_produced_it(collab: CollabStore):
    view = collab.add_view(
        s.SavedViewRequest(name="Failing refunds", screen="/runs", query="?status=failed"),
        author="raj",
    )
    assert view.screen == "runs"
    assert collab.views("runs") == [view]
    assert collab.delete_view(view.id)
    assert collab.views() == []


def test_incident_ownership_records_who_moved_it_and_when(collab: CollabStore):
    collab.update_incident(
        "errors-1", s.IncidentUpdate(status="investigating", assignee="raj"), author="raj"
    )
    state = collab.incident_state("errors-1")
    assert state["status"] == "investigating"
    assert state["assignee"] == "raj"
    assert state["history"][0]["by"] == "raj"

    collab.update_incident("errors-1", s.IncidentUpdate(status="resolved"), author="sam")
    state = collab.incident_state("errors-1")
    assert state["status"] == "resolved"
    assert state["assignee"] == "raj", "an update must not clear what it did not set"
    assert [entry["status"] for entry in state["history"]] == ["investigating", "resolved"]


def test_what_a_person_wrote_survives_an_index_rebuild(store: LocalStore, collab: CollabStore):
    """The one thing the console cannot re-derive must not live in the cache."""
    comment = collab.add_comment(
        s.CommentRequest(subject="run:run_1", body="keep me"), author="raj"
    )
    index_path = Path(RunIndex(store).path)
    index_path.unlink()
    RunIndex(store).refresh()

    assert CollabStore(store).comments("run:run_1") == [comment]

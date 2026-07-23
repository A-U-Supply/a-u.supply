"""Endpoint tests for the Marginalia read API (annotations list + counts)."""

import uuid

import pytest

from server.models import Annotation
from tests.conftest import make_media_item


def _make_annotation(db_session, media_item_id, **kwargs):
    defaults = {
        "id": str(uuid.uuid4()),
        "media_item_id": media_item_id,
        "kind": "comment",
        "source": "user",
        "position_seconds": 12.0,
        "body": "nice transition",
    }
    defaults.update(kwargs)
    a = Annotation(**defaults)
    db_session.add(a)
    db_session.commit()
    db_session.refresh(a)
    return a


class TestListAnnotations:
    def test_404_for_missing_item(self, client, auth_headers):
        resp = client.get("/api/media/nope/annotations", headers=auth_headers)
        assert resp.status_code == 404

    def test_empty(self, client, auth_headers, db_session):
        item = make_media_item(db_session)
        resp = client.get(f"/api/media/{item.id}/annotations", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["annotations"] == []
        assert body["inherited"] == []
        assert body["parent"] is None

    def test_returns_nested_replies(self, client, auth_headers, db_session, test_user):
        item = make_media_item(db_session)
        parent = _make_annotation(db_session, item.id, author_id=test_user.id)
        reply = _make_annotation(
            db_session,
            item.id,
            parent_id=parent.id,
            body="agreed — fixing in the next bounce",
        )
        cue = _make_annotation(
            db_session, item.id, kind="cue", source="wav_cue", label="Verse", body=None, position_seconds=3.5
        )

        resp = client.get(f"/api/media/{item.id}/annotations", headers=auth_headers)
        assert resp.status_code == 200
        annotations = resp.json()["annotations"]
        assert len(annotations) == 2
        first = next(a for a in annotations if a["id"] == parent.id)
        assert first["author"]["name"] == "Test User"
        assert [r["id"] for r in first["replies"]] == [reply.id]
        cue_doc = next(a for a in annotations if a["kind"] == "cue")
        assert cue_doc["label"] == "Verse"
        assert cue_doc["source"] == "wav_cue"
        assert cue_doc["position_seconds"] == 3.5

    def test_inheritance_from_parent_session(self, client, auth_headers, db_session):
        session_item = make_media_item(
            db_session, media_type="session", mime_type="application/octet-stream", filename="Song.logicx"
        )
        child = make_media_item(
            db_session,
            media_type="audio",
            mime_type="audio/wav",
            filename="kick.wav",
            parent_media_item_id=session_item.id,
        )
        _make_annotation(
            db_session, session_item.id, kind="cue", source="midi", label="Chorus", body=None, position_seconds=30.0
        )
        _make_annotation(
            db_session, session_item.id, kind="comment", source="user", body="session-level chat"
        )

        resp = client.get(f"/api/media/{child.id}/annotations", headers=auth_headers)
        body = resp.json()
        assert body["parent"] == {"id": session_item.id, "filename": "Song.logicx"}
        # only cues are inherited, not comments
        assert len(body["inherited"]) == 1
        assert body["inherited"][0]["label"] == "Chorus"

    def test_requires_auth(self, client, db_session):
        item = make_media_item(db_session)
        resp = client.get(f"/api/media/{item.id}/annotations")
        assert resp.status_code in (401, 403)


class TestAnnotationCounts:
    def test_counts_by_kind_and_resolution(self, client, auth_headers, db_session, test_user):
        from datetime import datetime, timezone

        item = make_media_item(db_session)
        other = make_media_item(db_session)
        _make_annotation(db_session, item.id, kind="comment", source="user")
        _make_annotation(
            db_session,
            item.id,
            kind="comment",
            source="user",
            resolved_at=datetime.now(timezone.utc),
            resolved_by=test_user.id,
        )
        _make_annotation(db_session, item.id, kind="cue", source="wav_cue", label="Verse", body=None)
        _make_annotation(db_session, other.id, kind="comment", source="user")

        resp = client.get(
            f"/api/media/annotations/counts?media_ids={item.id},{other.id},missing",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        counts = resp.json()["counts"]
        assert counts[item.id] == {"comments": 2, "cues": 1, "unresolved": 1}
        assert counts[other.id] == {"comments": 1, "cues": 0, "unresolved": 1}
        assert counts["missing"] == {"comments": 0, "cues": 0, "unresolved": 0}

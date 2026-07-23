"""Endpoint tests for the Marginalia write API (create/edit/resolve/delete/search)."""

import uuid

import pytest

from server.models import Annotation
from tests.conftest import make_media_item


def _make_annotation(db_session, media_item_id, author_id, **kwargs):
    defaults = {
        "id": str(uuid.uuid4()),
        "media_item_id": media_item_id,
        "kind": "comment",
        "source": "user",
        "position_seconds": 12.0,
        "body": "original take",
        "author_id": author_id,
    }
    defaults.update(kwargs)
    a = Annotation(**defaults)
    db_session.add(a)
    db_session.commit()
    db_session.refresh(a)
    return a


@pytest.fixture(autouse=True)
def mock_meili_sync(monkeypatch):
    monkeypatch.setattr("server.marginalia_api.sync_annotation", lambda *a, **k: None)
    monkeypatch.setattr("server.marginalia_api.delete_annotation_doc", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def mock_slack(monkeypatch):
    calls = []

    def fake_notify(event_type, user, **payload):
        calls.append((event_type, payload))

    import server.slack_notifier

    monkeypatch.setattr(server.slack_notifier, "notify_immediate", fake_notify)
    return calls


class TestCreate:
    def test_create_comment(self, client, auth_headers, db_session, mock_slack):
        item = make_media_item(db_session, media_type="audio", mime_type="audio/wav")
        resp = client.post(
            f"/api/media/{item.id}/annotations",
            json={"kind": "comment", "position_seconds": 62.5, "body": "fix this transition"},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["kind"] == "comment"
        assert body["source"] == "user"
        assert body["position_seconds"] == 62.5
        assert body["body"] == "fix this transition"
        assert body["author"]["name"] == "Test User"
        assert body["touched_by_user"] is True
        assert body["resolved"] is False

        # Slack notification fired with a human timestamp
        assert mock_slack and mock_slack[0][0] == "latent.annotation_created"
        assert mock_slack[0][1]["timestamp"] == "1:02"

    def test_comment_requires_body(self, client, auth_headers, db_session):
        item = make_media_item(db_session)
        resp = client.post(
            f"/api/media/{item.id}/annotations",
            json={"kind": "comment", "position_seconds": 1.0, "body": ""},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_cue_needs_no_text(self, client, auth_headers, db_session):
        item = make_media_item(db_session)
        resp = client.post(
            f"/api/media/{item.id}/annotations",
            json={"kind": "cue", "position_seconds": 30.0},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["kind"] == "cue"
        assert resp.json()["label"] is None

    def test_404_for_missing_item(self, client, auth_headers):
        resp = client.post(
            "/api/media/nope/annotations",
            json={"kind": "comment", "position_seconds": 1.0, "body": "hi"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_reply_one_level(self, client, auth_headers, db_session, test_user):
        item = make_media_item(db_session)
        parent = _make_annotation(db_session, item.id, test_user.id)

        resp = client.post(
            f"/api/media/{item.id}/annotations",
            json={"kind": "comment", "position_seconds": 12.0, "body": "reply", "parent_id": parent.id},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        reply = resp.json()
        assert reply["parent_id"] == parent.id

        # Replies to replies are rejected
        resp = client.post(
            f"/api/media/{item.id}/annotations",
            json={"kind": "comment", "position_seconds": 12.0, "body": "nested", "parent_id": reply["id"]},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_reply_parent_must_match_item(self, client, auth_headers, db_session, test_user):
        item = make_media_item(db_session)
        other = make_media_item(db_session)
        parent = _make_annotation(db_session, other.id, test_user.id)
        resp = client.post(
            f"/api/media/{item.id}/annotations",
            json={"kind": "comment", "position_seconds": 1.0, "body": "x", "parent_id": parent.id},
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestEdit:
    def test_edit_body_and_position(self, client, auth_headers, db_session, test_user):
        item = make_media_item(db_session)
        a = _make_annotation(db_session, item.id, test_user.id)
        resp = client.patch(
            f"/api/annotations/{a.id}",
            json={"body": "better take", "position_seconds": 15.5},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["body"] == "better take"
        assert body["position_seconds"] == 15.5
        assert body["touched_by_user"] is True

    def test_edit_rejects_empty_comment_body(self, client, auth_headers, db_session, test_user):
        item = make_media_item(db_session)
        a = _make_annotation(db_session, item.id, test_user.id)
        resp = client.patch(
            f"/api/annotations/{a.id}",
            json={"body": "   "},
            headers=auth_headers,
        )
        assert resp.status_code == 400


class TestResolve:
    def test_toggle(self, client, auth_headers, db_session, test_user):
        item = make_media_item(db_session)
        a = _make_annotation(db_session, item.id, test_user.id)

        resp = client.post(f"/api/annotations/{a.id}/resolve", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["resolved"] is True
        assert resp.json()["resolved_at"] is not None

        resp = client.post(f"/api/annotations/{a.id}/resolve", headers=auth_headers)
        assert resp.json()["resolved"] is False
        assert resp.json()["resolved_at"] is None


class TestDelete:
    def test_delete_cascades_replies(self, client, auth_headers, db_session, test_user):
        item = make_media_item(db_session)
        parent = _make_annotation(db_session, item.id, test_user.id)
        _make_annotation(db_session, item.id, test_user.id, parent_id=parent.id, body="reply")

        resp = client.delete(f"/api/annotations/{parent.id}", headers=auth_headers)
        assert resp.status_code == 204
        assert db_session.query(Annotation).filter_by(media_item_id=item.id).count() == 0


class TestSearch:
    def test_search_passes_filters(self, client, auth_headers, monkeypatch):
        captured = {}

        class FakeIndex:
            def search(self, q, params):
                captured["q"] = q
                captured["params"] = params
                return {"hits": [{"id": "a1"}], "totalHits": 1, "page": 1, "hitsPerPage": 20}

        class FakeClient:
            def index(self, name):
                captured["index"] = name
                return FakeIndex()

        monkeypatch.setattr("server.marginalia_api.get_client", lambda: FakeClient())
        resp = client.get(
            "/api/annotations?q=drop&project_id=proj1&kind=comment&resolved=false",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert captured["index"] == "marginalia"
        assert captured["q"] == "drop"
        assert 'project_ids = "proj1"' in captured["params"]["filter"]
        assert 'kind = "comment"' in captured["params"]["filter"]
        assert "resolved = false" in captured["params"]["filter"]

    def test_search_503_when_meili_down(self, client, auth_headers, monkeypatch):
        class DownClient:
            def index(self, name):
                raise ConnectionError("meili down")

        monkeypatch.setattr("server.marginalia_api.get_client", lambda: DownClient())
        resp = client.get("/api/annotations?q=x", headers=auth_headers)
        assert resp.status_code == 503

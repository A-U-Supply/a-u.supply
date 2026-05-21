"""Vote endpoint coverage (issue #318).

Covers toggle / switch / retract semantics, idempotency, voter-list
visibility, and the per-user `my votes` lookup. Meilisearch sync is
mocked out — the unit under test is the SQLite write path.
"""

from unittest.mock import patch

import pytest

from tests.conftest import make_media_item


@pytest.fixture(autouse=True)
def _stub_vote_sync():
    """Don't fire real Meili partial updates inside unit tests."""
    with patch("server.search_api.vote_sync.schedule") as m:
        yield m


@pytest.fixture(autouse=True)
def _stub_queue_batched():
    with patch("server.search_api.queue_batched") as m:
        yield m


def test_upvote_returns_aggregates(client, auth_headers, db_session):
    item = make_media_item(db_session)
    r = client.post(f"/api/search/{item.id}/vote", json={"value": 1}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body == {"up_count": 1, "down_count": 0, "vote_score": 1, "my_vote": 1}


def test_downvote_returns_aggregates(client, auth_headers, db_session):
    item = make_media_item(db_session)
    r = client.post(f"/api/search/{item.id}/vote", json={"value": -1}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"up_count": 0, "down_count": 1, "vote_score": -1, "my_vote": -1}


def test_revote_same_direction_is_idempotent(client, auth_headers, db_session):
    item = make_media_item(db_session)
    client.post(f"/api/search/{item.id}/vote", json={"value": 1}, headers=auth_headers)
    r = client.post(f"/api/search/{item.id}/vote", json={"value": 1}, headers=auth_headers)
    assert r.json()["up_count"] == 1


def test_switch_direction(client, auth_headers, db_session):
    item = make_media_item(db_session)
    client.post(f"/api/search/{item.id}/vote", json={"value": 1}, headers=auth_headers)
    r = client.post(f"/api/search/{item.id}/vote", json={"value": -1}, headers=auth_headers)
    assert r.json() == {"up_count": 0, "down_count": 1, "vote_score": -1, "my_vote": -1}


def test_retract_with_zero(client, auth_headers, db_session):
    item = make_media_item(db_session)
    client.post(f"/api/search/{item.id}/vote", json={"value": 1}, headers=auth_headers)
    r = client.post(f"/api/search/{item.id}/vote", json={"value": 0}, headers=auth_headers)
    assert r.json() == {"up_count": 0, "down_count": 0, "vote_score": 0, "my_vote": 0}


def test_retract_with_no_prior_vote_is_noop(client, auth_headers, db_session):
    item = make_media_item(db_session)
    r = client.post(f"/api/search/{item.id}/vote", json={"value": 0}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["my_vote"] == 0


def test_vote_on_missing_media_404(client, auth_headers):
    r = client.post("/api/search/does-not-exist/vote", json={"value": 1}, headers=auth_headers)
    assert r.status_code == 404


def test_invalid_value_rejected(client, auth_headers, db_session):
    item = make_media_item(db_session)
    r = client.post(f"/api/search/{item.id}/vote", json={"value": 7}, headers=auth_headers)
    assert r.status_code == 422


def test_voters_endpoint_lists_both_buckets(client, auth_headers, db_session, test_user, test_member):
    item = make_media_item(db_session)
    # Promote the "member" user to admin so they're allowed to vote — the
    # endpoint requires admin scope, but the role bump is the cheapest
    # way to get a second voting identity in this test setup.
    test_member.role = "admin"
    db_session.commit()
    from server.auth import COOKIE_NAME, create_access_token

    member_headers = {"Cookie": f"{COOKIE_NAME}={create_access_token({'sub': test_member.email})}"}

    client.post(f"/api/search/{item.id}/vote", json={"value": 1}, headers=auth_headers)
    client.post(f"/api/search/{item.id}/vote", json={"value": -1}, headers=member_headers)

    r = client.get(f"/api/search/{item.id}/voters", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert [v["name"] for v in body["upvoters"]] == [test_user.name]
    assert [v["name"] for v in body["downvoters"]] == [test_member.name]


def test_my_votes_endpoint(client, auth_headers, db_session):
    a = make_media_item(db_session)
    b = make_media_item(db_session)
    c = make_media_item(db_session)
    client.post(f"/api/search/{a.id}/vote", json={"value": 1}, headers=auth_headers)
    client.post(f"/api/search/{b.id}/vote", json={"value": -1}, headers=auth_headers)
    # c is not voted on
    r = client.get("/api/search/votes/mine", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert set(body["up"]) == {a.id}
    assert set(body["down"]) == {b.id}
    assert c.id not in body["up"] and c.id not in body["down"]


def test_vote_requires_admin(client, member_auth_headers, db_session):
    item = make_media_item(db_session)
    r = client.post(f"/api/search/{item.id}/vote", json={"value": 1}, headers=member_auth_headers)
    assert r.status_code == 403


def test_vote_schedules_meili_sync(client, auth_headers, db_session, _stub_vote_sync):
    item = make_media_item(db_session)
    client.post(f"/api/search/{item.id}/vote", json={"value": 1}, headers=auth_headers)
    _stub_vote_sync.assert_called_with(item.id)


def test_idempotent_vote_does_not_re_emit_activity(client, auth_headers, db_session, _stub_queue_batched):
    item = make_media_item(db_session)
    client.post(f"/api/search/{item.id}/vote", json={"value": 1}, headers=auth_headers)
    client.post(f"/api/search/{item.id}/vote", json={"value": 1}, headers=auth_headers)
    assert _stub_queue_batched.call_count == 1

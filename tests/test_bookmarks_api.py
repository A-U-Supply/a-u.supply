"""Bookmarks API — list endpoint inlines display metadata.

Toggle + list round-trip is covered to lock in the JSON shape the
bookmarks page consumes (see src/pages/admin/bookmarks.astro). The
inlined `meta` field replaces N follow-up fetches the client used to do
per item, so the assertion that it's present and shaped right is what
prevents the perf regression from sneaking back in.
"""

from datetime import date

from server.models import Bookmark, Release, Track
from tests.conftest import make_media_item


def _bookmark(db_session, user, target_type, target_id):
    bm = Bookmark(user_id=user.id, target_type=target_type, target_id=str(target_id))
    db_session.add(bm)
    db_session.commit()


def _make_release(db_session, user, code="A-U M0001.X", title="A Release", cover=False):
    rel = Release(
        product_code=code,
        title=title,
        status="published",
        created_by=user.id,
        release_date=date(2024, 1, 1),
        cover_art_path="cover.jpg" if cover else None,
    )
    db_session.add(rel)
    db_session.commit()
    db_session.refresh(rel)
    return rel


def _make_track(db_session, release, title="A Track", with_audio=True):
    t = Track(
        release_id=release.id,
        title=title,
        track_number=1,
        audio_file_path="track.mp3" if with_audio else None,
    )
    db_session.add(t)
    db_session.commit()
    db_session.refresh(t)
    return t


def test_list_inlines_media_item_meta(client, auth_headers, db_session, test_user):
    item = make_media_item(db_session, filename="cool.png", media_type="image")
    _bookmark(db_session, test_user, "media_item", item.id)

    r = client.get("/api/bookmarks", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    meta = body["items"][0]["meta"]
    assert meta["name"] == "cool.png"
    assert meta["media_type"] == "image"
    assert meta["thumb"] == f"/api/media/{item.id}/thumbnail"
    assert meta["thumb_sm"] == f"/api/media/{item.id}/thumbnail?size=sm"
    assert meta["href"] == f"/admin/search/detail?id={item.id}"
    assert meta["playable"] is False


def test_list_inlines_release_meta(client, auth_headers, db_session, test_user):
    rel = _make_release(db_session, test_user, code="A-U M9999.Z", title="Demo", cover=True)
    _bookmark(db_session, test_user, "release", rel.product_code)

    r = client.get("/api/bookmarks", headers=auth_headers)
    meta = r.json()["items"][0]["meta"]
    assert meta["name"] == "Demo"
    assert meta["media_type"] == "release"
    assert meta["thumb"] == "/api/releases/A-U%20M9999.Z/cover?size=thumb"
    assert meta["href"] == "/catalog/release?code=A-U%20M9999.Z"
    assert meta["playable"] is False


def test_list_release_meta_omits_thumb_when_no_cover(client, auth_headers, db_session, test_user):
    rel = _make_release(db_session, test_user, code="A-U M0002.Y", cover=False)
    _bookmark(db_session, test_user, "release", rel.product_code)

    meta = client.get("/api/bookmarks", headers=auth_headers).json()["items"][0]["meta"]
    assert meta["thumb"] is None


def test_list_inlines_track_meta(client, auth_headers, db_session, test_user):
    rel = _make_release(db_session, test_user, code="A-U M3000.T", title="Album", cover=True)
    t = _make_track(db_session, rel, title="Side A")
    _bookmark(db_session, test_user, "track", t.id)

    meta = client.get("/api/bookmarks", headers=auth_headers).json()["items"][0]["meta"]
    assert meta["name"] == "Side A"
    assert meta["media_type"] == "audio"
    assert meta["sub"] == "Album"
    assert meta["playable"] is True
    assert meta["stream_url"] == f"/api/releases/A-U%20M3000.T/tracks/{t.id}/stream"


def test_list_meta_is_null_for_deleted_target(client, auth_headers, db_session, test_user):
    # Bookmark a media_item id that doesn't exist (target was deleted).
    _bookmark(db_session, test_user, "media_item", "ghost-id")

    body = client.get("/api/bookmarks", headers=auth_headers).json()
    assert body["total"] == 1
    assert body["items"][0]["meta"] is None


def test_list_bulk_loads_in_three_queries(client, auth_headers, db_session, test_user):
    """Sanity check: ten bookmarks must not produce ten per-item lookups."""
    for i in range(10):
        item = make_media_item(db_session, filename=f"f{i}.png")
        _bookmark(db_session, test_user, "media_item", item.id)

    r = client.get("/api/bookmarks", headers=auth_headers)
    body = r.json()
    assert body["total"] == 10
    assert all(it["meta"] is not None for it in body["items"])
    assert all(it["meta"]["name"].startswith("f") for it in body["items"])


def test_toggle_then_list_roundtrip(client, auth_headers, db_session):
    item = make_media_item(db_session, filename="x.png")
    r = client.post(
        "/api/bookmarks",
        json={"target_type": "media_item", "target_id": item.id},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json() == {"bookmarked": True}

    listed = client.get("/api/bookmarks", headers=auth_headers).json()
    assert listed["total"] == 1
    assert listed["items"][0]["target_id"] == item.id
    assert listed["items"][0]["meta"]["name"] == "x.png"

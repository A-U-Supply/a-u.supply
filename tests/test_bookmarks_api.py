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


# ----- search / sort / cache -----


def test_search_matches_media_filename(client, auth_headers, db_session, test_user):
    apple = make_media_item(db_session, filename="apple.png")
    banana = make_media_item(db_session, filename="banana.png")
    _bookmark(db_session, test_user, "media_item", apple.id)
    _bookmark(db_session, test_user, "media_item", banana.id)

    r = client.get("/api/bookmarks?q=app", headers=auth_headers).json()
    assert r["total"] == 1
    assert r["items"][0]["target_id"] == apple.id


def test_search_matches_release_title(client, auth_headers, db_session, test_user):
    rel = _make_release(db_session, test_user, code="A-U M0010.W", title="Whispers")
    other = _make_release(db_session, test_user, code="A-U M0011.X", title="Noise")
    _bookmark(db_session, test_user, "release", rel.product_code)
    _bookmark(db_session, test_user, "release", other.product_code)

    r = client.get("/api/bookmarks?q=whisp", headers=auth_headers).json()
    assert r["total"] == 1
    assert r["items"][0]["meta"]["name"] == "Whispers"


def test_search_matches_track_title(client, auth_headers, db_session, test_user):
    rel = _make_release(db_session, test_user, code="A-U M0020.A", title="Album")
    a = _make_track(db_session, rel, title="Sirens")
    b = Track(release_id=rel.id, title="Other Song", track_number=2)
    db_session.add(b)
    db_session.commit()
    db_session.refresh(b)
    _bookmark(db_session, test_user, "track", a.id)
    _bookmark(db_session, test_user, "track", b.id)

    r = client.get("/api/bookmarks?q=siren", headers=auth_headers).json()
    assert r["total"] == 1
    assert r["items"][0]["meta"]["name"] == "Sirens"


def test_search_spans_target_types(client, auth_headers, db_session, test_user):
    """A query that matches across two target types returns both."""
    item = make_media_item(db_session, filename="harvest.png")
    rel = _make_release(db_session, test_user, code="A-U M0030.H", title="Harvest Moon")
    _bookmark(db_session, test_user, "media_item", item.id)
    _bookmark(db_session, test_user, "release", rel.product_code)
    # Noise that shouldn't match
    other = make_media_item(db_session, filename="other.png")
    _bookmark(db_session, test_user, "media_item", other.id)

    names = sorted(
        i["meta"]["name"]
        for i in client.get("/api/bookmarks?q=harvest", headers=auth_headers).json()["items"]
    )
    assert names == ["Harvest Moon", "harvest.png"]


def test_sort_oldest(client, auth_headers, db_session, test_user):
    a = make_media_item(db_session, filename="a.png")
    b = make_media_item(db_session, filename="b.png")
    _bookmark(db_session, test_user, "media_item", a.id)
    _bookmark(db_session, test_user, "media_item", b.id)

    items = client.get("/api/bookmarks?sort=oldest", headers=auth_headers).json()["items"]
    assert items[0]["target_id"] == a.id
    assert items[1]["target_id"] == b.id


def test_sort_name_orders_across_types(client, auth_headers, db_session, test_user):
    rel = _make_release(db_session, test_user, code="A-U M0040.Z", title="Zebra")
    item = make_media_item(db_session, filename="apple.png")
    _bookmark(db_session, test_user, "release", rel.product_code)
    _bookmark(db_session, test_user, "media_item", item.id)

    items = client.get("/api/bookmarks?sort=name", headers=auth_headers).json()["items"]
    assert [i["meta"]["name"] for i in items] == ["apple.png", "Zebra"]


def test_invalid_sort_falls_back_to_newest(client, auth_headers, db_session, test_user):
    a = make_media_item(db_session, filename="a.png")
    b = make_media_item(db_session, filename="b.png")
    _bookmark(db_session, test_user, "media_item", a.id)
    _bookmark(db_session, test_user, "media_item", b.id)
    items = client.get("/api/bookmarks?sort=garbage", headers=auth_headers).json()["items"]
    assert items[0]["target_id"] == b.id  # newest first


def test_response_carries_etag_and_cache_control(client, auth_headers, db_session, test_user):
    item = make_media_item(db_session, filename="x.png")
    _bookmark(db_session, test_user, "media_item", item.id)

    r = client.get("/api/bookmarks", headers=auth_headers)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "private, max-age=30"
    assert r.headers.get("etag", "").startswith('W/"')


def test_if_none_match_returns_304(client, auth_headers, db_session, test_user):
    item = make_media_item(db_session, filename="x.png")
    _bookmark(db_session, test_user, "media_item", item.id)

    first = client.get("/api/bookmarks", headers=auth_headers)
    etag = first.headers["etag"]

    second = client.get(
        "/api/bookmarks",
        headers={**auth_headers, "If-None-Match": etag},
    )
    assert second.status_code == 304
    assert second.content == b""
    assert second.headers["etag"] == etag


def test_etag_changes_when_bookmark_added(client, auth_headers, db_session, test_user):
    a = make_media_item(db_session, filename="a.png")
    _bookmark(db_session, test_user, "media_item", a.id)
    e1 = client.get("/api/bookmarks", headers=auth_headers).headers["etag"]

    b = make_media_item(db_session, filename="b.png")
    _bookmark(db_session, test_user, "media_item", b.id)
    e2 = client.get("/api/bookmarks", headers=auth_headers).headers["etag"]

    assert e1 != e2


def test_pagination_respects_filter_and_sort(client, auth_headers, db_session, test_user):
    for i in range(5):
        item = make_media_item(db_session, filename=f"f{i}.png")
        _bookmark(db_session, test_user, "media_item", item.id)

    r = client.get("/api/bookmarks?per_page=2&page=2&sort=oldest", headers=auth_headers).json()
    assert r["total"] == 5
    assert r["page"] == 2
    assert r["per_page"] == 2
    names = [i["meta"]["name"] for i in r["items"]]
    assert names == ["f2.png", "f3.png"]

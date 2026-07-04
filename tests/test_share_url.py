"""OG unfurl tests for the short share URL /m/{id} and the admin detail route.

Both routes inject the same OpenGraph block into the built detail HTML; only
`og:url` differs. These tests pin that contract.
"""

import pytest

from tests.conftest import make_media_item


@pytest.fixture
def dist_html(monkeypatch, tmp_path):
    """Point main.DIST_DIR at a tmp dir containing a built detail index.html."""
    detail_dir = tmp_path / "admin" / "search" / "detail"
    detail_dir.mkdir(parents=True)
    index = detail_dir / "index.html"
    index.write_text("<!doctype html><html><head></head><body>detail</body></html>")
    import main
    monkeypatch.setattr(main, "DIST_DIR", tmp_path)
    return index


class TestShareUrlOg:
    """GET /m/{id} injects OG tags with the canonical share URL."""

    def test_tags_present_with_known_id(self, client, db_session, dist_html):
        item = make_media_item(db_session, filename="cool_sample.png")
        resp = client.get(f"/m/{item.id}")
        assert resp.status_code == 200
        body = resp.text
        assert f'<meta property="og:title" content="cool_sample.png" />' in body
        assert '<meta property="og:type" content="website" />' in body
        assert '<meta name="twitter:card" content="summary_large_image" />' in body
        # image URL uses the id and the public og-thumb route
        assert (
            f'<meta property="og:image" content="https://a-u.supply/api/media/{item.id}/og-thumb" />'
            in body
        )
        # canonical URL is the clean share path
        assert (
            f'<meta property="og:url" content="https://a-u.supply/m/{item.id}" />' in body
        )

    def test_unknown_id_returns_bare_html(self, client, db_session, dist_html):
        resp = client.get("/m/nonexistent-id")
        assert resp.status_code == 200
        # No OG block when the item isn't found — bare HTML returned as-is
        assert "og:title" not in resp.text
        assert "og:url" not in resp.text

    def test_image_dimensions_injected_when_present(self, client, db_session, dist_html):
        from server.models import MediaImageMeta

        item = make_media_item(db_session, filename="with_meta.jpg")
        db_session.add(MediaImageMeta(media_item_id=item.id, width=640, height=480, format="png"))
        db_session.commit()

        resp = client.get(f"/m/{item.id}")
        body = resp.text
        assert '<meta property="og:image:width" content="640" />' in body
        assert '<meta property="og:image:height" content="480" />' in body


class TestAdminDetailOgRegression:
    """The refactor must keep /admin/search/detail?id=... unfurling the old URL."""

    def test_admin_detail_uses_admin_canonical_url(self, client, db_session, dist_html):
        item = make_media_item(db_session, filename="admin_path.png")
        resp = client.get(f"/admin/search/detail?id={item.id}")
        assert resp.status_code == 200
        body = resp.text
        assert (
            f'<meta property="og:url" content="https://a-u.supply/admin/search/detail?id={item.id}" />'
            in body
        )
        assert f'<meta property="og:title" content="admin_path.png" />' in body

    def test_admin_detail_without_id_returns_bare_html(self, client, db_session, dist_html):
        resp = client.get("/admin/search/detail")
        assert resp.status_code == 200
        assert "og:title" not in resp.text
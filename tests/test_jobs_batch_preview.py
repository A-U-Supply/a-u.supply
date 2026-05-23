"""Tests for POST /api/jobs/batch/preview.

The preview endpoint reuses _build_batch_pool — the same code path the
real batch submit uses — so these tests double as a regression net for
the exclude_processed_* flags. multi_search is mocked; the real test is
that the SQL exclusion + media-type filter run as expected.
"""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from server.models import AppDefinition, Job
from tests.conftest import make_media_item


FAKE_MANIFEST = """
[meta]
name = 'fakeapp'

[input]
media_types = ['image']
"""


@pytest.fixture
def fakeapp(db_session):
    app_def = AppDefinition(
        name="fakeapp",
        display_name="Fake App",
        image="fake:latest",
        manifest=FAKE_MANIFEST,
    )
    db_session.add(app_def)
    db_session.commit()
    return app_def


@pytest.fixture
def image_items(db_session):
    """Three image MediaItems that multi_search will be made to return."""
    return [make_media_item(db_session) for _ in range(3)]


def _hits_for(items):
    return {
        "hits": [{"id": it.id, "filename": it.filename} for it in items],
        "total": len(items),
        "facets": {},
        "page": 1,
        "per_page": 10000,
    }


def _completed_job(db_session, user, *, app_name, input_ids, params):
    job = Job(
        id=str(uuid.uuid4()),
        app_name=app_name,
        status="completed",
        input_items=json.dumps(input_ids),
        params=json.dumps(params),
        created_by=user.id,
        completed_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


class TestBatchPreview:
    def test_requires_auth(self, client, fakeapp):
        resp = client.post(
            "/api/jobs/batch/preview", json={"app_name": "fakeapp"}
        )
        assert resp.status_code == 401

    def test_unknown_app_404s(self, client, auth_headers):
        resp = client.post(
            "/api/jobs/batch/preview",
            json={"app_name": "nope"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_returns_full_pool_when_no_exclusion(
        self, client, auth_headers, fakeapp, image_items
    ):
        with patch(
            "server.search_client.multi_search", return_value=_hits_for(image_items)
        ):
            resp = client.post(
                "/api/jobs/batch/preview",
                json={"app_name": "fakeapp"},
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.json() == {"count": 3, "total": 3}

    def test_app_exclusion_removes_already_processed(
        self,
        client,
        auth_headers,
        db_session,
        test_user,
        fakeapp,
        image_items,
    ):
        # One of the three items was already an input to a prior fakeapp job.
        _completed_job(
            db_session,
            test_user,
            app_name="fakeapp",
            input_ids=[image_items[0].id],
            params={"recipe": "alpha"},
        )

        with patch(
            "server.search_client.multi_search", return_value=_hits_for(image_items)
        ):
            resp = client.post(
                "/api/jobs/batch/preview",
                json={
                    "app_name": "fakeapp",
                    "shuffle": {"exclude_processed_by_app": True},
                },
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.json() == {"count": 2, "total": 3}

    def test_recipe_exclusion_scopes_to_matching_recipe(
        self,
        client,
        auth_headers,
        db_session,
        test_user,
        fakeapp,
        image_items,
    ):
        # item[0] was processed with recipe=alpha; item[1] with recipe=beta.
        # Asking to exclude recipe=alpha should remove only item[0], even
        # though both are "already processed by this app".
        _completed_job(
            db_session,
            test_user,
            app_name="fakeapp",
            input_ids=[image_items[0].id],
            params={"recipe": "alpha"},
        )
        _completed_job(
            db_session,
            test_user,
            app_name="fakeapp",
            input_ids=[image_items[1].id],
            params={"recipe": "beta"},
        )

        with patch(
            "server.search_client.multi_search", return_value=_hits_for(image_items)
        ):
            resp = client.post(
                "/api/jobs/batch/preview",
                json={
                    "app_name": "fakeapp",
                    "shuffle": {
                        "exclude_processed_by_app": True,
                        "exclude_processed_by_recipe": True,
                    },
                    "params": {"recipe": "alpha"},
                    "random_recipe": False,
                },
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.json() == {"count": 2, "total": 3}

    def test_recipe_exclusion_silently_skipped_under_random_recipe(
        self,
        client,
        auth_headers,
        db_session,
        test_user,
        fakeapp,
        image_items,
    ):
        # Pre-existing job for recipe=alpha. With random_recipe=true, the
        # recipe-exclusion flag should be ignored — pool only loses items
        # via the app-exclusion (which is also set here).
        _completed_job(
            db_session,
            test_user,
            app_name="fakeapp",
            input_ids=[image_items[0].id],
            params={"recipe": "alpha"},
        )

        with patch(
            "server.search_client.multi_search", return_value=_hits_for(image_items)
        ):
            resp = client.post(
                "/api/jobs/batch/preview",
                json={
                    "app_name": "fakeapp",
                    "shuffle": {
                        "exclude_processed_by_app": True,
                        "exclude_processed_by_recipe": True,
                    },
                    "params": {"recipe": "alpha"},
                    "random_recipe": True,
                },
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.json() == {"count": 2, "total": 3}, "app exclusion still applies; recipe flag no-ops"

    def test_app_without_input_media_types_returns_zero(
        self, client, auth_headers, db_session
    ):
        app_def = AppDefinition(
            name="noinput",
            display_name="No Input",
            image="x:latest",
            manifest="[meta]\nname = 'noinput'\n",
        )
        db_session.add(app_def)
        db_session.commit()

        resp = client.post(
            "/api/jobs/batch/preview",
            json={"app_name": "noinput"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json() == {"count": 0, "total": 0}

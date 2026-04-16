"""Tests for the midden — the shared 24h trash bucket with the inverse-rule
(anyone BUT the original discarder can rescue by indexing).
"""

import pathlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from auth import COOKIE_NAME, create_access_token, hash_password
from models import AppDefinition, Job, JobOutput, User


@pytest.fixture
def bob(db_session):
    user = User(
        email="bob@test.com",
        name="bob",
        password_hash=hash_password("bobpass"),
        role="admin",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def bob_headers(bob):
    token = create_access_token({"sub": bob.email})
    return {"Cookie": f"{COOKIE_NAME}={token}"}


@pytest.fixture
def fakeapp(db_session):
    app_def = AppDefinition(
        name="fakeapp",
        display_name="Fakeapp",
        image="fake:latest",
        manifest="[meta]\nname = 'fakeapp'\n",
    )
    db_session.add(app_def)
    db_session.commit()
    return app_def


@pytest.fixture
def alice_job_with_outputs(db_session, test_user, fakeapp, tmp_path, monkeypatch):
    """A completed job owned by `test_user` (alice) with two outputs on disk."""
    job_data_dir = tmp_path / "job-data"
    monkeypatch.setenv("JOB_DATA_DIR", str(job_data_dir))
    # jobs_api caches JOB_DATA_DIR at import time — patch it directly
    import jobs_api
    monkeypatch.setattr(jobs_api, "JOB_DATA_DIR", job_data_dir)
    import main as _main
    monkeypatch.setattr(_main, "_reap_midden_sync", _main._reap_midden_sync)

    job = Job(
        id=str(uuid.uuid4()),
        app_name="fakeapp",
        status="completed",
        input_items="[]",
        params="{}",
        created_by=test_user.id,
        completed_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    db_session.flush()

    out_dir = job_data_dir / job.id / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for i in range(2):
        fp = out_dir / f"out{i}.png"
        fp.write_bytes(b"\x89PNG fake")
        o = JobOutput(
            id=str(uuid.uuid4()),
            job_id=job.id,
            filename=f"out{i}.png",
            file_path=f"out{i}.png",
            media_type="image",
            file_size_bytes=fp.stat().st_size,
        )
        db_session.add(o)
        outputs.append(o)
    db_session.commit()
    for o in outputs:
        db_session.refresh(o)
    return {"job": job, "outputs": outputs, "out_dir": out_dir}


def test_slop_bucket_is_per_user(client, auth_headers, bob_headers, alice_job_with_outputs):
    """Alice sees her own slop; Bob sees none of it."""
    r = client.get("/api/jobs/outputs/unindexed", headers=auth_headers)
    assert r.json()["total"] == 2

    r = client.get("/api/jobs/outputs/unindexed", headers=bob_headers)
    assert r.json()["total"] == 0


def test_bulk_discard_soft_deletes_to_midden(
    client, auth_headers, alice_job_with_outputs
):
    """Discarding from the slop bucket soft-deletes (keeps files on disk)."""
    ids = [o.id for o in alice_job_with_outputs["outputs"]]
    r = client.post(
        "/api/jobs/outputs/bulk-discard",
        json={"output_ids": ids},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["discarded"] == 2

    # Files still on disk
    out_dir = alice_job_with_outputs["out_dir"]
    assert (out_dir / "out0.png").exists()
    assert (out_dir / "out1.png").exists()

    # Slop bucket is empty
    r = client.get("/api/jobs/outputs/unindexed", headers=auth_headers)
    assert r.json()["total"] == 0


def test_midden_is_visible_to_all(
    client, auth_headers, bob_headers, alice_job_with_outputs
):
    """Everyone sees midden items; is_own_discard flags the discarder."""
    ids = [o.id for o in alice_job_with_outputs["outputs"]]
    client.post(
        "/api/jobs/outputs/bulk-discard",
        json={"output_ids": ids},
        headers=auth_headers,
    )

    alice_view = client.get("/api/jobs/outputs/midden", headers=auth_headers).json()
    bob_view = client.get("/api/jobs/outputs/midden", headers=bob_headers).json()

    assert alice_view["total"] == 2
    assert bob_view["total"] == 2
    assert all(o["is_own_discard"] for o in alice_view["outputs"])
    assert not any(o["is_own_discard"] for o in bob_view["outputs"])


def test_inverse_rule_excludes_discarder_from_indexable_ids(
    client, auth_headers, bob_headers, alice_job_with_outputs
):
    """Alice discarded them — only Bob (and others) can index from the midden."""
    ids = [o.id for o in alice_job_with_outputs["outputs"]]
    client.post(
        "/api/jobs/outputs/bulk-discard",
        json={"output_ids": ids},
        headers=auth_headers,
    )

    alice_ids = client.get(
        "/api/jobs/outputs/midden/ids", headers=auth_headers
    ).json()["ids"]
    bob_ids = client.get(
        "/api/jobs/outputs/midden/ids", headers=bob_headers
    ).json()["ids"]

    assert alice_ids == []
    assert len(bob_ids) == 2


def test_self_index_from_midden_is_blocked(
    client, auth_headers, alice_job_with_outputs
):
    """The discarder gets 403 with the 'absolution' message if they try to rescue their own."""
    outs = alice_job_with_outputs["outputs"]
    job = alice_job_with_outputs["job"]
    client.post(
        "/api/jobs/outputs/bulk-discard",
        json={"output_ids": [o.id for o in outs]},
        headers=auth_headers,
    )

    r = client.post(
        f"/api/jobs/{job.id}/outputs/{outs[0].id}/index",
        headers=auth_headers,
    )
    assert r.status_code == 403
    assert "absolution" in r.json()["detail"].lower()


def test_discard_ownership_enforced(client, auth_headers, bob, db_session, fakeapp):
    """Alice can't discard Bob's outputs (per-job endpoint enforces ownership)."""
    job = Job(
        id=str(uuid.uuid4()),
        app_name="fakeapp",
        status="completed",
        input_items="[]",
        params="{}",
        created_by=bob.id,
    )
    db_session.add(job)
    db_session.commit()

    r = client.delete(f"/api/jobs/{job.id}/outputs", headers=auth_headers)
    assert r.status_code == 403


def test_reaper_hard_deletes_past_ttl(db_session, alice_job_with_outputs, monkeypatch):
    """Reaper sweep removes files + rows for items past the 24h cutoff, leaves fresh ones."""
    outs = alice_job_with_outputs["outputs"]
    out_dir = alice_job_with_outputs["out_dir"]
    old_id = outs[0].id
    fresh_id = outs[1].id

    now = datetime.now(timezone.utc)
    outs[0].discarded_at = now - timedelta(hours=25)
    outs[0].discarded_by = 1
    outs[1].discarded_at = now - timedelta(hours=1)
    outs[1].discarded_by = 1
    db_session.commit()

    # The reaper opens its own Session via the module-level `engine`. Point it
    # at the in-memory test engine so it sees the same data.
    import main as _main
    from sqlalchemy.orm import sessionmaker

    test_engine = db_session.bind
    monkeypatch.setattr(_main, "engine", test_engine)

    result = _main._reap_midden_sync()
    assert result["purged"] == 1
    assert not (out_dir / "out0.png").exists()
    assert (out_dir / "out1.png").exists()

    # Fresh session to observe the reaper's commit without colliding with the
    # test's session identity map (StaticPool shares one connection).
    Session = sessionmaker(bind=test_engine)
    s = Session()
    try:
        assert s.query(JobOutput).filter(JobOutput.id == old_id).count() == 0
        assert s.query(JobOutput).filter(JobOutput.id == fresh_id).count() == 1
    finally:
        s.close()

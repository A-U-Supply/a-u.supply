"""Tests for GET /api/jobs/{job_id}/outputs/{output_id}/thumbnail.

Covers both the indexed-output path (delegates to MediaItem resolver) and
the unindexed path (generates _thumb.webp on first hit for images; serves
placeholder SVG for audio/video).
"""

import os
import uuid
from datetime import datetime, timezone

import pytest
from PIL import Image

from models import AppDefinition, Job, JobOutput, MediaItem
from tests.conftest import make_media_item


def _write_png(path, size=(400, 300), color=(80, 160, 40)):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", size, color=color).save(path, format="PNG")


@pytest.fixture
def fakeapp(db_session):
    app_def = AppDefinition(
        name="fakeapp",
        display_name="Fake App",
        image="fake:latest",
        manifest="[meta]\nname = 'fakeapp'\n",
    )
    db_session.add(app_def)
    db_session.commit()
    return app_def


@pytest.fixture
def job_with_image_output(db_session, test_user, fakeapp, tmp_path, monkeypatch):
    """A completed job with one image output written to disk under JOB_DATA_DIR."""
    job_data_dir = tmp_path / "job-data"
    monkeypatch.setenv("JOB_DATA_DIR", str(job_data_dir))
    import jobs_api
    monkeypatch.setattr(jobs_api, "JOB_DATA_DIR", job_data_dir)

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

    src_path = out_dir / "img.png"
    _write_png(str(src_path))

    output = JobOutput(
        id=str(uuid.uuid4()),
        job_id=job.id,
        filename="img.png",
        file_path="img.png",
        # Prod stores coarse "image" for most rows (see existing midden
        # tests); exercise the common path explicitly.
        media_type="image",
        file_size_bytes=src_path.stat().st_size,
    )
    db_session.add(output)
    db_session.commit()
    db_session.refresh(output)

    return {"job": job, "output": output, "out_dir": out_dir, "src": src_path}


class TestUnindexedImageOutput:
    """Unindexed image output — thumbnail is generated on first hit."""

    def test_generates_md_thumbnail_on_first_hit(
        self, client, auth_headers, job_with_image_output
    ):
        job = job_with_image_output["job"]
        output = job_with_image_output["output"]
        out_dir = job_with_image_output["out_dir"]

        thumb_path = out_dir / "img_thumb.webp"
        assert not thumb_path.exists()

        resp = client.get(
            f"/api/jobs/{job.id}/outputs/{output.id}/thumbnail",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/webp"
        assert thumb_path.exists()
        assert "private" in resp.headers.get("Cache-Control", "")

    def test_generates_sm_thumbnail_on_first_hit(
        self, client, auth_headers, job_with_image_output
    ):
        job = job_with_image_output["job"]
        output = job_with_image_output["output"]
        out_dir = job_with_image_output["out_dir"]

        sm_path = out_dir / "img_thumb_sm.webp"
        assert not sm_path.exists()

        resp = client.get(
            f"/api/jobs/{job.id}/outputs/{output.id}/thumbnail?size=sm",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert sm_path.exists()
        with Image.open(sm_path) as img:
            assert max(img.size) == 128

    def test_generates_lg_thumbnail_on_first_hit(
        self, client, auth_headers, job_with_image_output
    ):
        job = job_with_image_output["job"]
        output = job_with_image_output["output"]
        out_dir = job_with_image_output["out_dir"]

        lg_path = out_dir / "img_thumb_lg.webp"
        assert not lg_path.exists()

        resp = client.get(
            f"/api/jobs/{job.id}/outputs/{output.id}/thumbnail?size=lg",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert lg_path.exists()
        # Source is 400x300 so lg falls back to source dims (no upscale)
        with Image.open(lg_path) as img:
            assert img.format == "WEBP"
            assert max(img.size) == 400

    def test_second_hit_reuses_cached_file(
        self, client, auth_headers, job_with_image_output
    ):
        job = job_with_image_output["job"]
        output = job_with_image_output["output"]
        out_dir = job_with_image_output["out_dir"]

        # First request generates the thumb
        client.get(
            f"/api/jobs/{job.id}/outputs/{output.id}/thumbnail",
            headers=auth_headers,
        )
        thumb_path = out_dir / "img_thumb.webp"
        first_mtime = thumb_path.stat().st_mtime

        # Second request — should not re-generate
        client.get(
            f"/api/jobs/{job.id}/outputs/{output.id}/thumbnail",
            headers=auth_headers,
        )
        assert thumb_path.stat().st_mtime == first_mtime

    def test_invalid_size_rejected(
        self, client, auth_headers, job_with_image_output
    ):
        job = job_with_image_output["job"]
        output = job_with_image_output["output"]
        resp = client.get(
            f"/api/jobs/{job.id}/outputs/{output.id}/thumbnail?size=xl",
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_unknown_output_404s(self, client, auth_headers, job_with_image_output):
        job = job_with_image_output["job"]
        resp = client.get(
            f"/api/jobs/{job.id}/outputs/nonexistent/thumbnail",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_missing_source_file_returns_placeholder(
        self, client, auth_headers, job_with_image_output
    ):
        """File purged from disk — return a placeholder, don't 404."""
        job = job_with_image_output["job"]
        output = job_with_image_output["output"]
        src = job_with_image_output["src"]
        src.unlink()

        resp = client.get(
            f"/api/jobs/{job.id}/outputs/{output.id}/thumbnail",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/svg+xml")


class TestMediaTypeFormat:
    """Both `media_type="image"` and `media_type="image/png"` exist in prod."""

    def _make_image_output(self, db_session, test_user, monkeypatch, tmp_path, mtype):
        job_data_dir = tmp_path / "job-data"
        monkeypatch.setenv("JOB_DATA_DIR", str(job_data_dir))
        import jobs_api
        monkeypatch.setattr(jobs_api, "JOB_DATA_DIR", job_data_dir)

        job = Job(
            id=str(uuid.uuid4()),
            app_name="fakeapp",
            status="completed",
            input_items="[]",
            params="{}",
            created_by=test_user.id,
        )
        db_session.add(job)
        db_session.flush()

        out_dir = job_data_dir / job.id / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        src = out_dir / f"x-{mtype.replace('/', '_')}.png"
        _write_png(str(src))

        output = JobOutput(
            id=str(uuid.uuid4()),
            job_id=job.id,
            filename=src.name,
            file_path=src.name,
            media_type=mtype,
            file_size_bytes=src.stat().st_size,
        )
        db_session.add(output)
        db_session.commit()
        db_session.refresh(output)
        return job, output

    def test_coarse_image_string_generates_thumb(
        self, client, auth_headers, db_session, test_user, fakeapp, tmp_path, monkeypatch
    ):
        job, output = self._make_image_output(
            db_session, test_user, monkeypatch, tmp_path, "image"
        )
        resp = client.get(
            f"/api/jobs/{job.id}/outputs/{output.id}/thumbnail",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/webp"

    def test_mime_image_png_also_generates_thumb(
        self, client, auth_headers, db_session, test_user, fakeapp, tmp_path, monkeypatch
    ):
        job, output = self._make_image_output(
            db_session, test_user, monkeypatch, tmp_path, "image/png"
        )
        resp = client.get(
            f"/api/jobs/{job.id}/outputs/{output.id}/thumbnail",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/webp"


class TestUnindexedAudioVideoOutput:
    """Audio/video unindexed outputs — placeholder, no ffmpeg in request path."""

    @pytest.fixture
    def audio_output(self, db_session, test_user, fakeapp, tmp_path, monkeypatch):
        job_data_dir = tmp_path / "job-data"
        monkeypatch.setenv("JOB_DATA_DIR", str(job_data_dir))
        import jobs_api
        monkeypatch.setattr(jobs_api, "JOB_DATA_DIR", job_data_dir)

        job = Job(
            id=str(uuid.uuid4()),
            app_name="fakeapp",
            status="completed",
            input_items="[]",
            params="{}",
            created_by=test_user.id,
        )
        db_session.add(job)
        db_session.flush()

        out_dir = job_data_dir / job.id / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        src = out_dir / "clip.wav"
        src.write_bytes(b"RIFFfake")

        output = JobOutput(
            id=str(uuid.uuid4()),
            job_id=job.id,
            filename="clip.wav",
            file_path="clip.wav",
            media_type="audio/wav",
            file_size_bytes=src.stat().st_size,
        )
        db_session.add(output)
        db_session.commit()
        db_session.refresh(output)
        return {"job": job, "output": output}

    def test_audio_returns_placeholder_svg(self, client, auth_headers, audio_output):
        job = audio_output["job"]
        output = audio_output["output"]
        resp = client.get(
            f"/api/jobs/{job.id}/outputs/{output.id}/thumbnail",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/svg+xml")
        assert b"aria-label=\"Audio\"" in resp.content


class TestIndexedOutputDelegates:
    """Indexed output — endpoint reuses the MediaItem resolver."""

    def test_delegates_to_media_item_thumbnail(
        self, client, auth_headers, db_session, tmp_media_dir,
        test_user, fakeapp, tmp_path, monkeypatch,
    ):
        job_data_dir = tmp_path / "job-data"
        monkeypatch.setenv("JOB_DATA_DIR", str(job_data_dir))
        import jobs_api
        monkeypatch.setattr(jobs_api, "JOB_DATA_DIR", job_data_dir)

        # Make a MediaItem with a _thumb.webp on the search-media side
        rel = "image/2026-04/indexed.png"
        item = make_media_item(db_session, file_path=rel)
        thumb_rel = "image/2026-04/indexed_thumb.webp"
        thumb_full = os.path.join(tmp_media_dir, thumb_rel)
        os.makedirs(os.path.dirname(thumb_full), exist_ok=True)
        Image.new("RGB", (50, 50), color=(255, 0, 0)).save(thumb_full, format="WEBP")

        # Now make a Job + indexed JobOutput pointing at that MediaItem
        job = Job(
            id=str(uuid.uuid4()),
            app_name="fakeapp",
            status="completed",
            input_items="[]",
            params="{}",
            created_by=test_user.id,
        )
        db_session.add(job)
        db_session.flush()
        output = JobOutput(
            id=str(uuid.uuid4()),
            job_id=job.id,
            filename="indexed.png",
            file_path="indexed.png",
            media_type="image/png",
            file_size_bytes=100,
            indexed=True,
            media_item_id=item.id,
        )
        db_session.add(output)
        db_session.commit()
        db_session.refresh(output)

        resp = client.get(
            f"/api/jobs/{job.id}/outputs/{output.id}/thumbnail",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        # Served the WebP thumb via the MediaItem resolver (rather than 404
        # because no file exists under JOB_DATA_DIR for this indexed output).
        assert resp.headers["content-type"] == "image/webp"

"""Tests for media search engine SQLAlchemy models."""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from tests.conftest import make_media_item, make_media_source


class TestMediaItem:
    """Tests for the MediaItem model."""

    def test_create_media_item(self, db_session):
        from models import MediaItem

        item = MediaItem(
            id=str(uuid.uuid4()),
            sha256="a" * 64,
            filename="photo.jpg",
            file_path="image/2026-04/aaaaaaaa_photo.jpg",
            media_type="image",
            file_size_bytes=2048,
            mime_type="image/jpeg",
        )
        db_session.add(item)
        db_session.commit()

        fetched = db_session.query(MediaItem).filter(MediaItem.id == item.id).first()
        assert fetched is not None
        assert fetched.sha256 == "a" * 64
        assert fetched.filename == "photo.jpg"
        assert fetched.media_type == "image"
        assert fetched.file_size_bytes == 2048
        assert fetched.mime_type == "image/jpeg"
        assert fetched.created_at is not None
        assert fetched.updated_at is not None

    def test_sha256_unique_constraint(self, db_session):
        from models import MediaItem

        sha = "b" * 64
        item1 = MediaItem(
            id=str(uuid.uuid4()),
            sha256=sha,
            filename="file1.png",
            file_path="image/2026-04/bbbbbbbb_file1.png",
            media_type="image",
            file_size_bytes=100,
            mime_type="image/png",
        )
        db_session.add(item1)
        db_session.commit()

        item2 = MediaItem(
            id=str(uuid.uuid4()),
            sha256=sha,
            filename="file2.png",
            file_path="image/2026-04/bbbbbbbb_file2.png",
            media_type="image",
            file_size_bytes=200,
            mime_type="image/png",
        )
        db_session.add(item2)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_description_nullable(self, db_session):
        item = make_media_item(db_session, description=None)
        assert item.description is None

        item2 = make_media_item(db_session, description="A cool photo")
        assert item2.description == "A cool photo"


class TestMediaSource:
    """Tests for the MediaSource model."""

    def test_create_source(self, db_session):
        from models import MediaSource

        item = make_media_item(db_session)
        source = MediaSource(
            media_item_id=item.id,
            source_type="slack_file",
            source_channel="#general",
            slack_file_id="F12345",
            slack_message_ts="1234567890.123456",
            slack_message_text="Check this out",
            reaction_count=5,
        )
        db_session.add(source)
        db_session.commit()

        assert source.id is not None
        assert source.source_type == "slack_file"
        assert source.source_channel == "#general"
        assert source.reaction_count == 5

    def test_source_relationship(self, db_session):
        item = make_media_item(db_session)
        make_media_source(db_session, item.id, source_type="manual_upload")
        make_media_source(db_session, item.id, source_type="slack_file")

        db_session.refresh(item)
        assert len(item.sources) == 2
        source_types = {s.source_type for s in item.sources}
        assert source_types == {"manual_upload", "slack_file"}


class TestMediaMetaModels:
    """Tests for type-specific metadata models."""

    def test_image_meta(self, db_session):
        from models import MediaImageMeta

        item = make_media_item(db_session, media_type="image")
        meta = MediaImageMeta(
            media_item_id=item.id,
            width=1920,
            height=1080,
            format="JPEG",
            dominant_colors='["#1a1a2e", "#e94560"]',
            caption="A sunset",
        )
        db_session.add(meta)
        db_session.commit()

        db_session.refresh(item)
        assert item.image_meta is not None
        assert item.image_meta.width == 1920
        assert item.image_meta.height == 1080
        assert item.image_meta.format == "JPEG"

    def test_audio_meta(self, db_session):
        from models import MediaAudioMeta

        item = make_media_item(db_session, media_type="audio", mime_type="audio/wav")
        meta = MediaAudioMeta(
            media_item_id=item.id,
            duration_seconds=120.5,
            sample_rate=44100,
            channels=2,
            bit_depth=16,
            transcript="Hello world",
            transcript_confidence=0.95,
        )
        db_session.add(meta)
        db_session.commit()

        db_session.refresh(item)
        assert item.audio_meta is not None
        assert item.audio_meta.duration_seconds == 120.5
        assert item.audio_meta.sample_rate == 44100

    def test_video_meta(self, db_session):
        from models import MediaVideoMeta

        item = make_media_item(db_session, media_type="video", mime_type="video/mp4")
        meta = MediaVideoMeta(
            media_item_id=item.id,
            duration_seconds=60.0,
            width=1280,
            height=720,
            fps=29.97,
            thumbnail_path="video/2026-04/thumb.webp",
        )
        db_session.add(meta)
        db_session.commit()

        db_session.refresh(item)
        assert item.video_meta is not None
        assert item.video_meta.fps == 29.97


class TestMediaTag:
    """Tests for the MediaTag model."""

    def test_create_tag(self, db_session):
        from models import MediaTag

        item = make_media_item(db_session)
        tag = MediaTag(media_item_id=item.id, tag="drums")
        db_session.add(tag)
        db_session.commit()

        db_session.refresh(item)
        assert len(item.tags) == 1
        assert item.tags[0].tag == "drums"

    def test_unique_constraint_same_item_same_tag(self, db_session):
        from models import MediaTag

        item = make_media_item(db_session)
        tag1 = MediaTag(media_item_id=item.id, tag="drums")
        db_session.add(tag1)
        db_session.commit()

        tag2 = MediaTag(media_item_id=item.id, tag="drums")
        db_session.add(tag2)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_same_tag_different_items(self, db_session):
        from models import MediaTag

        item1 = make_media_item(db_session)
        item2 = make_media_item(db_session)

        db_session.add(MediaTag(media_item_id=item1.id, tag="drums"))
        db_session.add(MediaTag(media_item_id=item2.id, tag="drums"))
        db_session.commit()  # Should not raise


class TestTagVocabulary:
    """Tests for the TagVocabulary model."""

    def test_create_vocabulary_entry(self, db_session):
        from models import TagVocabulary

        vocab = TagVocabulary(tag="drums", usage_count=5)
        db_session.add(vocab)
        db_session.commit()

        fetched = db_session.query(TagVocabulary).filter(TagVocabulary.tag == "drums").first()
        assert fetched is not None
        assert fetched.usage_count == 5
        assert fetched.created_at is not None


class TestApiKey:
    """Tests for the ApiKey model."""

    def test_create_api_key(self, db_session, test_user):
        from models import ApiKey

        key = ApiKey(
            user_id=test_user.id,
            key_hash="fakehash",
            key_prefix="au_abcde",
            label="test key",
            scope="read",
        )
        db_session.add(key)
        db_session.commit()

        assert key.id is not None
        assert key.revoked_at is None
        assert key.last_used_at is None


class TestExtractionFailure:
    """Tests for the ExtractionFailure model."""

    def test_create_failure(self, db_session):
        from models import ExtractionFailure

        item = make_media_item(db_session)
        failure = ExtractionFailure(
            media_item_id=item.id,
            extraction_type="image_metadata",
            error_message="Pillow not installed",
            attempts=1,
            resolved=False,
        )
        db_session.add(failure)
        db_session.commit()

        assert failure.id is not None
        assert failure.resolved is False


class TestCascadeDeletes:
    """Tests for cascade delete behavior."""

    def test_delete_media_item_cascades_to_sources(self, db_session):
        from models import MediaItem, MediaSource

        item = make_media_item(db_session)
        make_media_source(db_session, item.id)
        make_media_source(db_session, item.id, source_type="slack_file")

        item_id = item.id
        db_session.delete(item)
        db_session.commit()

        sources = db_session.query(MediaSource).filter(MediaSource.media_item_id == item_id).all()
        assert len(sources) == 0

    def test_delete_media_item_cascades_to_tags(self, db_session):
        from models import MediaItem, MediaTag

        item = make_media_item(db_session)
        db_session.add(MediaTag(media_item_id=item.id, tag="drums"))
        db_session.add(MediaTag(media_item_id=item.id, tag="bass"))
        db_session.commit()

        item_id = item.id
        db_session.delete(item)
        db_session.commit()

        tags = db_session.query(MediaTag).filter(MediaTag.media_item_id == item_id).all()
        assert len(tags) == 0

    def test_delete_media_item_cascades_to_meta(self, db_session):
        from models import MediaImageMeta

        item = make_media_item(db_session)
        meta = MediaImageMeta(
            media_item_id=item.id, width=100, height=100, format="PNG"
        )
        db_session.add(meta)
        db_session.commit()

        item_id = item.id
        db_session.delete(item)
        db_session.commit()

        assert db_session.query(MediaImageMeta).filter(
            MediaImageMeta.media_item_id == item_id
        ).first() is None

    def test_delete_media_item_cascades_to_extraction_failures(self, db_session):
        from models import ExtractionFailure

        item = make_media_item(db_session)
        db_session.add(
            ExtractionFailure(
                media_item_id=item.id,
                extraction_type="ffprobe",
                error_message="failed",
            )
        )
        db_session.commit()

        item_id = item.id
        db_session.delete(item)
        db_session.commit()

        assert db_session.query(ExtractionFailure).filter(
            ExtractionFailure.media_item_id == item_id
        ).first() is None

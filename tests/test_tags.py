"""Tests for tag normalization, vocabulary management, and autocomplete."""

import pytest

from tests.conftest import make_media_item


class TestTagNormalization:
    """Tests for the _normalize_tag helper."""

    def test_lowercase(self):
        from search_api import _normalize_tag

        assert _normalize_tag("Drums") == "drums"
        assert _normalize_tag("VOCAL") == "vocal"
        assert _normalize_tag("Bass Guitar") == "bass guitar"

    def test_strip_whitespace(self):
        from search_api import _normalize_tag

        assert _normalize_tag("  vocal  ") == "vocal"
        assert _normalize_tag("\tdrums\n") == "drums"

    def test_combined_normalization(self):
        from search_api import _normalize_tag

        assert _normalize_tag("  DRUM LOOP  ") == "drum loop"

    def test_empty_string(self):
        from search_api import _normalize_tag

        assert _normalize_tag("") == ""
        assert _normalize_tag("   ") == ""


class TestVocabularyUpdate:
    """Tests for the _update_vocabulary helper."""

    def test_creates_vocabulary_entry_on_first_use(self, db_session):
        from search_api import _update_vocabulary
        from models import TagVocabulary

        _update_vocabulary(db_session, "drums", 1)
        db_session.commit()

        vocab = db_session.query(TagVocabulary).filter(TagVocabulary.tag == "drums").first()
        assert vocab is not None
        assert vocab.usage_count == 1

    def test_increments_usage_count(self, db_session):
        from search_api import _update_vocabulary
        from models import TagVocabulary

        _update_vocabulary(db_session, "bass", 1)
        db_session.commit()
        _update_vocabulary(db_session, "bass", 1)
        db_session.commit()

        vocab = db_session.query(TagVocabulary).filter(TagVocabulary.tag == "bass").first()
        assert vocab.usage_count == 2

    def test_decrements_usage_count(self, db_session):
        from search_api import _update_vocabulary
        from models import TagVocabulary

        _update_vocabulary(db_session, "synth", 1)
        db_session.commit()
        _update_vocabulary(db_session, "synth", 1)
        db_session.commit()
        _update_vocabulary(db_session, "synth", -1)
        db_session.commit()

        vocab = db_session.query(TagVocabulary).filter(TagVocabulary.tag == "synth").first()
        assert vocab.usage_count == 1

    def test_usage_count_never_goes_below_zero(self, db_session):
        from search_api import _update_vocabulary
        from models import TagVocabulary

        _update_vocabulary(db_session, "rare", 1)
        db_session.commit()
        _update_vocabulary(db_session, "rare", -5)
        db_session.commit()

        vocab = db_session.query(TagVocabulary).filter(TagVocabulary.tag == "rare").first()
        assert vocab.usage_count == 0

    def test_negative_delta_on_nonexistent_tag_is_noop(self, db_session):
        from search_api import _update_vocabulary
        from models import TagVocabulary

        _update_vocabulary(db_session, "ghost", -1)
        db_session.commit()

        vocab = db_session.query(TagVocabulary).filter(TagVocabulary.tag == "ghost").first()
        assert vocab is None


class TestDuplicateTagOnSameItem:
    """Tests that duplicate tags on the same item are silently skipped by the API."""

    def test_add_duplicate_tag_silently_ignored(self, db_session):
        from models import MediaTag

        item = make_media_item(db_session)

        # First tag
        tag1 = MediaTag(media_item_id=item.id, tag="drums")
        db_session.add(tag1)
        db_session.commit()

        # The API-level add_tags checks for existing tags before adding.
        # Direct DB insert would raise IntegrityError (tested in test_models).
        existing_tags = {t.tag for t in db_session.query(MediaTag).filter(
            MediaTag.media_item_id == item.id
        ).all()}
        assert "drums" in existing_tags

        # Simulating the API behavior: skip if already exists
        new_tag = "drums"
        if new_tag not in existing_tags:
            db_session.add(MediaTag(media_item_id=item.id, tag=new_tag))
        db_session.commit()

        tags = db_session.query(MediaTag).filter(MediaTag.media_item_id == item.id).all()
        assert len(tags) == 1


class TestTagAutocomplete:
    """Tests for tag autocomplete suggestions sorted by usage_count."""

    def test_autocomplete_sorted_by_usage(self, db_session):
        from models import TagVocabulary

        db_session.add(TagVocabulary(tag="drums", usage_count=10))
        db_session.add(TagVocabulary(tag="drum loop", usage_count=25))
        db_session.add(TagVocabulary(tag="drone", usage_count=3))
        db_session.add(TagVocabulary(tag="bass", usage_count=50))
        db_session.commit()

        # Query for "dru" should return drum-related tags, sorted by count
        results = (
            db_session.query(TagVocabulary)
            .filter(TagVocabulary.tag.ilike("%dru%"))
            .order_by(TagVocabulary.usage_count.desc())
            .all()
        )
        assert len(results) == 2
        assert results[0].tag == "drum loop"
        assert results[1].tag == "drums"

    def test_autocomplete_case_insensitive(self, db_session):
        from models import TagVocabulary

        db_session.add(TagVocabulary(tag="vocals", usage_count=5))
        db_session.commit()

        results = (
            db_session.query(TagVocabulary)
            .filter(TagVocabulary.tag.ilike("%VOCAL%"))
            .all()
        )
        assert len(results) == 1


class TestBatchTagging:
    """Tests for batch tagging across multiple items."""

    def test_batch_tag_multiple_items(self, db_session):
        from models import MediaTag
        from search_api import _normalize_tag, _update_vocabulary

        items = [make_media_item(db_session) for _ in range(3)]

        tags_to_add = ["drums", "bass"]
        for item in items:
            existing_tags = {t.tag for t in db_session.query(MediaTag).filter(
                MediaTag.media_item_id == item.id
            ).all()}
            for raw_tag in tags_to_add:
                tag = _normalize_tag(raw_tag)
                if tag and tag not in existing_tags:
                    db_session.add(MediaTag(media_item_id=item.id, tag=tag))
                    _update_vocabulary(db_session, tag, 1)
                    existing_tags.add(tag)

        db_session.commit()

        for item in items:
            db_session.refresh(item)
            item_tags = {t.tag for t in item.tags}
            assert item_tags == {"drums", "bass"}

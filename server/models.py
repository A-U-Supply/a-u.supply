import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker


DATABASE_URL = "sqlite:///data/au.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    # Without this, SQLite silently ignores every ON DELETE CASCADE in our
    # schema — deleting a media_items row leaves orphan project_items rows
    # behind, which surface in the UI as "(unknown)" entries.
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def _utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="member")  # "admin" or "member"
    created_at = Column(DateTime, default=_utcnow)
    lemmy_user_id = Column(Integer, nullable=True)
    lemmy_token_encrypted = Column(String, nullable=True)


class SlackUserMapping(Base):
    __tablename__ = "slack_user_mappings"

    slack_user_id = Column(String, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    email = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    user = relationship("User")


# --- Release Catalog Models ---

release_entities = Table(
    "release_entities",
    Base.metadata,
    Column("release_id", Integer, ForeignKey("releases.id", ondelete="CASCADE"), primary_key=True),
    Column("entity_id", Integer, ForeignKey("entities.id", ondelete="RESTRICT"), primary_key=True),
    Column("position", Integer, nullable=False, default=0),
    Column("role", String, nullable=True),
)


class Entity(Base):
    __tablename__ = "entities"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    slug = Column(String, unique=True, nullable=False)
    description = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    releases = relationship("Release", secondary=release_entities, back_populates="entities")


class Release(Base):
    __tablename__ = "releases"

    id = Column(Integer, primary_key=True, index=True)
    product_code = Column(String, unique=True, nullable=False, index=True)
    title = Column(String, nullable=False)
    release_date = Column(Date, nullable=True)
    cover_art_path = Column(String, nullable=True)
    status = Column(String, nullable=False, default="draft")
    category = Column(String, nullable=True)
    description = Column(String, nullable=True)
    format_specs = Column(String, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)
    latent_id = Column(String, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)

    entities = relationship(
        "Entity",
        secondary=release_entities,
        back_populates="releases",
        order_by=release_entities.c.position,
    )
    tracks = relationship("Track", back_populates="release", order_by="Track.track_number", cascade="all, delete-orphan")
    distribution_links = relationship("DistributionLink", back_populates="release", cascade="all, delete-orphan")
    metadata_pairs = relationship("ReleaseMetadata", back_populates="release", cascade="all, delete-orphan")
    creator = relationship("User")


class Track(Base):
    __tablename__ = "tracks"
    __table_args__ = (UniqueConstraint("release_id", "track_number"),)

    id = Column(Integer, primary_key=True, index=True)
    release_id = Column(Integer, ForeignKey("releases.id", ondelete="CASCADE"), nullable=False)
    title = Column(String, nullable=False)
    track_number = Column(Integer, nullable=False)
    audio_file_path = Column(String, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    release = relationship("Release", back_populates="tracks")


class DistributionLink(Base):
    __tablename__ = "distribution_links"

    id = Column(Integer, primary_key=True, index=True)
    release_id = Column(Integer, ForeignKey("releases.id", ondelete="CASCADE"), nullable=False)
    platform = Column(String, nullable=False)
    url = Column(String, nullable=False)
    label = Column(String, nullable=True)

    release = relationship("Release", back_populates="distribution_links")


class ReleaseMetadata(Base):
    __tablename__ = "release_metadata"
    __table_args__ = (UniqueConstraint("release_id", "key"),)

    id = Column(Integer, primary_key=True, index=True)
    release_id = Column(Integer, ForeignKey("releases.id", ondelete="CASCADE"), nullable=False)
    key = Column(String, nullable=False)
    value = Column(String, nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)

    release = relationship("Release", back_populates="metadata_pairs")


# --- Media Search Engine Models ---


class MediaItem(Base):
    __tablename__ = "media_items"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    sha256 = Column(String, unique=True, nullable=False)
    filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    media_type = Column(String, nullable=False)  # image, audio, video
    file_size_bytes = Column(Integer, nullable=False)
    mime_type = Column(String, nullable=False)
    description = Column(String, nullable=True)
    output_index = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    sources = relationship("MediaSource", back_populates="media_item", cascade="all, delete-orphan")
    tags = relationship("MediaTag", back_populates="media_item", cascade="all, delete-orphan")
    image_meta = relationship("MediaImageMeta", back_populates="media_item", uselist=False, cascade="all, delete-orphan")
    audio_meta = relationship("MediaAudioMeta", back_populates="media_item", uselist=False, cascade="all, delete-orphan")
    video_meta = relationship("MediaVideoMeta", back_populates="media_item", uselist=False, cascade="all, delete-orphan")
    session_meta = relationship("MediaSessionMeta", uselist=False, cascade="all, delete-orphan")
    extraction_failures = relationship("ExtractionFailure", back_populates="media_item", cascade="all, delete-orphan")


class MediaSource(Base):
    __tablename__ = "media_sources"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    media_item_id = Column(String, ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False)
    source_type = Column(String, nullable=False)  # slack_file, slack_link, manual_upload
    source_channel = Column(String, nullable=True)
    uploader_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    slack_file_id = Column(String, nullable=True)
    slack_message_ts = Column(String, nullable=True)
    slack_message_text = Column(String, nullable=True)
    slack_reactions = Column(String, nullable=True)  # JSON stored as text
    reaction_count = Column(Integer, nullable=False, default=0)
    source_url = Column(String, nullable=True)
    source_metadata = Column(String, nullable=True)  # JSON stored as text
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    media_item = relationship("MediaItem", back_populates="sources")
    uploader = relationship("User")


class MediaSlackShare(Base):
    __tablename__ = "media_slack_shares"
    __table_args__ = (UniqueConstraint("media_item_id", "channel_name"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    media_item_id = Column(String, ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False)
    channel_name = Column(String, nullable=False)  # "img-junkyard" or "sample-sale"
    slack_file_id = Column(String, nullable=True)
    sent_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    sent_at = Column(DateTime, nullable=False, default=_utcnow)


class MediaImageMeta(Base):
    __tablename__ = "media_image_meta"

    media_item_id = Column(String, ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True, unique=True)
    width = Column(Integer, nullable=False)
    height = Column(Integer, nullable=False)
    format = Column(String, nullable=False)
    dominant_colors = Column(String, nullable=True)  # JSON stored as text
    caption = Column(String, nullable=True)

    media_item = relationship("MediaItem", back_populates="image_meta")


class MediaAudioMeta(Base):
    __tablename__ = "media_audio_meta"

    media_item_id = Column(String, ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True, unique=True)
    duration_seconds = Column(Float, nullable=False)
    sample_rate = Column(Integer, nullable=False)
    channels = Column(Integer, nullable=False)
    bit_depth = Column(Integer, nullable=True)
    transcript = Column(String, nullable=True)
    transcript_confidence = Column(Float, nullable=True)
    acoustic_tags = Column(String, nullable=True)  # JSON stored as text

    media_item = relationship("MediaItem", back_populates="audio_meta")


class MediaVideoMeta(Base):
    __tablename__ = "media_video_meta"

    media_item_id = Column(String, ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True, unique=True)
    duration_seconds = Column(Float, nullable=False)
    width = Column(Integer, nullable=False)
    height = Column(Integer, nullable=False)
    fps = Column(Float, nullable=True)
    thumbnail_path = Column(String, nullable=True)
    audio_transcript = Column(String, nullable=True)
    transcript_confidence = Column(Float, nullable=True)

    media_item = relationship("MediaItem", back_populates="video_meta")


class MediaSessionMeta(Base):
    __tablename__ = "media_session_meta"

    media_item_id = Column(String, ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True, unique=True)
    tool = Column(String, nullable=False)
    tool_version = Column(String, nullable=True)
    original_bundle_name = Column(String, nullable=False)
    bundle_size_bytes = Column(Integer, nullable=False)
    notes = Column(String, nullable=True)

    media_item = relationship("MediaItem")


class MediaVote(Base):
    """Per-user up/down vote on a media item.

    value: +1 (acclaim) or -1 (disavow). Re-voting the same direction is
    treated as a retraction at the API layer (the row is deleted); switching
    direction updates value in place.
    """

    __tablename__ = "media_votes"
    __table_args__ = (CheckConstraint("value IN (-1, 1)", name="ck_media_votes_value"),)

    media_item_id = Column(String, ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    value = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    user = relationship("User")


class MediaTag(Base):
    __tablename__ = "media_tags"
    __table_args__ = (UniqueConstraint("media_item_id", "tag"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    media_item_id = Column(String, ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False)
    tag = Column(String, nullable=False)
    tagged_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    media_item = relationship("MediaItem", back_populates="tags")
    user = relationship("User")


class TagVocabulary(Base):
    __tablename__ = "tag_vocabulary"

    tag = Column(String, primary_key=True)
    usage_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    key_hash = Column(String, nullable=False)
    key_prefix = Column(String, nullable=False)
    label = Column(String, nullable=False)
    scope = Column(String, nullable=False)  # read, write, admin
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    last_used_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)

    user = relationship("User")


class ExtractionFailure(Base):
    __tablename__ = "extraction_failures"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    media_item_id = Column(String, ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False)
    extraction_type = Column(String, nullable=False)
    error_message = Column(String, nullable=False)
    attempts = Column(Integer, nullable=False, default=1)
    last_attempt_at = Column(DateTime, nullable=False, default=_utcnow)
    resolved = Column(Boolean, nullable=False, default=False)

    media_item = relationship("MediaItem", back_populates="extraction_failures")


# --- App Runner Models ---


class Workspace(Base):
    __tablename__ = "workspaces"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    creator = relationship("User")
    items = relationship("WorkspaceItem", back_populates="workspace", cascade="all, delete-orphan")


class WorkspaceItem(Base):
    __tablename__ = "workspace_items"
    __table_args__ = (UniqueConstraint("workspace_id", "media_item_id"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    workspace_id = Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    media_item_id = Column(String, ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False)
    added_at = Column(DateTime, nullable=False, default=_utcnow)

    workspace = relationship("Workspace", back_populates="items")
    media_item = relationship("MediaItem")


class AppDefinition(Base):
    __tablename__ = "app_definitions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, unique=True, nullable=False)
    display_name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    image = Column(String, nullable=False)
    manifest = Column(String, nullable=False)  # Full TOML stored as text
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    app_name = Column(String, ForeignKey("app_definitions.name"), nullable=False)
    status = Column(String, nullable=False, default="pending")  # pending, running, completed, failed, cancelled
    input_items = Column(String, nullable=False)  # JSON array of media_item_ids
    params = Column(String, nullable=False)  # JSON object of app-specific params
    priority = Column(Integer, nullable=False, default=100)  # lower = higher priority
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(String, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)
    log_tail = Column(String, nullable=True)  # Last N lines of container stdout/stderr
    batch_id = Column(String, nullable=True, index=True)

    app = relationship("AppDefinition")
    creator = relationship("User")
    outputs = relationship("JobOutput", back_populates="job", cascade="all, delete-orphan")


class JobOutput(Base):
    __tablename__ = "job_outputs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id = Column(String, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)  # Relative to /app/job-data/{job_id}/output/
    media_type = Column(String, nullable=True)  # image, audio, video, or null
    file_size_bytes = Column(Integer, nullable=True)
    indexed = Column(Boolean, nullable=False, default=False)
    media_item_id = Column(String, ForeignKey("media_items.id"), nullable=True)  # Set when indexed
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    discarded_at = Column(DateTime, nullable=True, index=True)  # Set when sent to midden; hard-deleted after 24h
    discarded_by = Column(Integer, ForeignKey("users.id"), nullable=True)  # User who discarded it (excluded from rescue/index)

    job = relationship("Job", back_populates="outputs")
    media_item = relationship("MediaItem")
    discarder = relationship("User", foreign_keys=[discarded_by])


class Bookmark(Base):
    __tablename__ = "bookmarks"
    __table_args__ = (UniqueConstraint("user_id", "target_type", "target_id"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    target_type = Column(String, nullable=False)  # media_item, release, track
    target_id = Column(String, nullable=False)  # str to handle both int IDs and uuid strings
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String, nullable=False, index=True)
    tier = Column(String, nullable=False)  # "immediate" or "batched"
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    payload = Column(String, nullable=True)  # JSON event details
    created_at = Column(DateTime, nullable=False, default=_utcnow, index=True)
    posted_at = Column(DateTime, nullable=True, index=True)  # null until posted to Slack

    user = relationship("User")


# --- Latents (pre-release workspace) Models ---


class Project(Base):
    """A Latent — a private, admin-only pre-release workspace."""

    __tablename__ = "projects"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    slug = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    kind = Column(String, nullable=False, default="other")  # album | video | zine | other
    status = Column(String, nullable=False, default="forming")  # forming | developing | fixing | abandoned
    description = Column(String, nullable=True)        # markdown, longer-form than the name
    metadata_json = Column(String, nullable=True)      # JSON dict of arbitrary {key: value}
    hero_media_item_id = Column(String, ForeignKey("media_items.id", ondelete="SET NULL"), nullable=True)
    lemmy_community_id = Column(Integer, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    creator = relationship("User")
    hero_media_item = relationship("MediaItem", foreign_keys=[hero_media_item_id])
    slots = relationship("ProjectSlot", back_populates="project", order_by="ProjectSlot.position", cascade="all, delete-orphan")
    items = relationship("ProjectItem", back_populates="project", cascade="all, delete-orphan")
    documents = relationship("ProjectDocument", back_populates="project", order_by="ProjectDocument.position", cascade="all, delete-orphan")


class ProjectSlot(Base):
    """An ordered, named subdivision of a Latent (e.g. a Track on an album)."""

    __tablename__ = "project_slots"
    __table_args__ = (UniqueConstraint("project_id", "position"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    position = Column(Integer, nullable=False)
    label = Column(String, nullable=False)
    notes = Column(String, nullable=True)
    notes_updated_at = Column(DateTime, nullable=True)
    status = Column(String, nullable=False, default="forming")  # forming | developing | fixed
    description = Column(String, nullable=True)      # markdown
    metadata_json = Column(String, nullable=True)    # JSON dict of arbitrary {key: value}
    # Optional GitHub repo file/dir association — populated by the link UI or
    # the manifest-sync flow. `repo_id` not declared as FK in code to avoid
    # circular import; enforced by the `projects` migration on prod.
    repo_id = Column(String, nullable=True, index=True)
    repo_path = Column(String, nullable=True)
    repo_ref = Column(String, nullable=True)         # commit SHA, branch, or tag
    run_command = Column(String, nullable=True)      # override for sandboxed runs
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    project = relationship("Project", back_populates="slots")
    pins = relationship("SlotPrimaryPin", back_populates="slot", cascade="all, delete-orphan")
    items = relationship("ProjectItem", back_populates="slot")


class ProjectItem(Base):
    """An attachment of a media_item to a Latent (and optionally a specific slot)."""

    __tablename__ = "project_items"
    __table_args__ = (UniqueConstraint("project_id", "slot_id", "media_item_id"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    slot_id = Column(String, ForeignKey("project_slots.id", ondelete="SET NULL"), nullable=True, index=True)
    media_item_id = Column(String, ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False, index=True)
    added_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    added_at = Column(DateTime, nullable=False, default=_utcnow)
    is_primary = Column(Boolean, nullable=False, default=False)  # star toggle — replaces the rigid per-type pin grid

    project = relationship("Project", back_populates="items")
    slot = relationship("ProjectSlot", back_populates="items")
    media_item = relationship("MediaItem")
    adder = relationship("User")


class SlotPrimaryPin(Base):
    """Per slot, per media type, the file pinned as the slot's current 'primary'."""

    __tablename__ = "slot_primary_pins"

    slot_id = Column(String, ForeignKey("project_slots.id", ondelete="CASCADE"), primary_key=True)
    media_type = Column(String, primary_key=True)  # image | audio | video | session
    media_item_id = Column(String, ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False)
    repo_run_id = Column(String, nullable=True)    # provenance: which run produced this pin

    slot = relationship("ProjectSlot", back_populates="pins")
    media_item = relationship("MediaItem")


class ProjectDocument(Base):
    """A named markdown document attached to a Latent."""

    __tablename__ = "project_documents"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    position = Column(Integer, nullable=False, default=0)
    name = Column(String, nullable=False)
    content = Column(String, nullable=False, default="")
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    project = relationship("Project", back_populates="documents")
    last_editor = relationship("User")
    revisions = relationship("ProjectDocumentRevision", back_populates="document", order_by="ProjectDocumentRevision.saved_at.desc()", cascade="all, delete-orphan")


class ProjectDocumentRevision(Base):
    """Full-content snapshots of a project document's history."""

    __tablename__ = "project_document_revisions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String, ForeignKey("project_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    content = Column(String, nullable=False)
    saved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    saved_at = Column(DateTime, nullable=False, default=_utcnow, index=True)

    document = relationship("ProjectDocument", back_populates="revisions")
    saver = relationship("User")


class FoldWatcherState(Base):
    """High-water marks for the fold (Lemmy) → #supply-side poller.

    One row per tracked stream — keys: ``last_community_id``, ``last_post_id``.
    Bootstrapped on first run so historical content isn't backflooded to Slack.
    """

    __tablename__ = "fold_watcher_state"

    key = Column(String, primary_key=True)
    value = Column(Integer, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class Thread(Base):
    """Generic, anchor-typed discussion thread backed by a Lemmy post."""

    __tablename__ = "threads"
    __table_args__ = (UniqueConstraint("lemmy_post_id"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    anchor_type = Column(String, nullable=False, index=True)  # project | slot | media_item
    anchor_id = Column(String, nullable=False, index=True)
    lemmy_post_id = Column(Integer, nullable=False)
    lemmy_community_id = Column(Integer, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    # Denormalized post title — written on create, refreshed lazily when the
    # thread is fetched individually. Source of truth stays Lemmy.
    title_cache = Column(String, nullable=True)

    creator = relationship("User")


class ProjectLink(Base):
    """Free-form URL pinned to a Latent or one of its slots.

    Typical use: a Slack permalink to the message where the band discussed
    a track, a Drive link, a SoundCloud preview. Anchored to either a
    project (project_id set, slot_id null) or a slot (slot_id set; project_id
    redundantly set for fast lookup by project).
    """

    __tablename__ = "project_links"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    slot_id = Column(String, ForeignKey("project_slots.id", ondelete="CASCADE"), nullable=True, index=True)
    url = Column(String, nullable=False)
    label = Column(String, nullable=True)              # optional display name
    kind = Column(String, nullable=False, default="link")   # auto-detected: slack, drive, soundcloud, youtube, …
    position = Column(Integer, nullable=False, default=0)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    project = relationship("Project")
    slot = relationship("ProjectSlot")
    creator = relationship("User")


# --- GitHub repo integration ---


class GithubToken(Base):
    """Encrypted personal-access tokens for private-repo access."""

    __tablename__ = "github_tokens"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    label = Column(String, nullable=False)
    scopes = Column(String, nullable=True)  # display only
    github_login = Column(String, nullable=True)  # captured at validation time
    token_encrypted = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    last_used_at = Column(DateTime, nullable=True)

    user = relationship("User")


class ProjectRepo(Base):
    """A GitHub repository linked to a Latent."""

    __tablename__ = "project_repos"
    __table_args__ = (UniqueConstraint("project_id"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    url = Column(String, nullable=False)             # canonical https URL
    provider = Column(String, nullable=False, default="github")
    owner = Column(String, nullable=False)
    repo_name = Column(String, nullable=False)
    default_branch = Column(String, nullable=False, default="main")
    visibility = Column(String, nullable=False, default="public")  # public | private
    github_token_id = Column(String, ForeignKey("github_tokens.id", ondelete="SET NULL"), nullable=True)
    last_sha = Column(String, nullable=True)
    last_synced_at = Column(DateTime, nullable=True)
    manifest_json = Column(String, nullable=True)    # cached parsed manifest
    webhook_secret = Column(String, nullable=True)   # for /api/github/webhook HMAC
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    project = relationship("Project")
    github_token = relationship("GithubToken")
    creator = relationship("User")


class RepoRun(Base):
    """Provenance for every sandboxed script execution out of a linked repo."""

    __tablename__ = "repo_runs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    slot_id = Column(String, ForeignKey("project_slots.id", ondelete="SET NULL"), nullable=True, index=True)
    repo_id = Column(String, ForeignKey("project_repos.id", ondelete="CASCADE"), nullable=False, index=True)
    job_id = Column(String, ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True)
    ref = Column(String, nullable=False)             # resolved commit SHA at run time
    command = Column(String, nullable=False)
    started_at = Column(DateTime, nullable=False, default=_utcnow)
    finished_at = Column(DateTime, nullable=True)
    exit_code = Column(Integer, nullable=True)
    outputs_json = Column(String, nullable=True)
    stderr_tail = Column(String, nullable=True)
    triggered_by = Column(Integer, ForeignKey("users.id"), nullable=False)

    project = relationship("Project")
    slot = relationship("ProjectSlot")
    repo = relationship("ProjectRepo")
    job = relationship("Job")
    runner = relationship("User")

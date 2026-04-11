from datetime import datetime, timezone

from sqlalchemy import (
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
    description = Column(String, nullable=True)
    format_specs = Column(String, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

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

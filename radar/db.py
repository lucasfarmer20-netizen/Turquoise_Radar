import os
from pathlib import Path

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from radar.schemas import utcnow


class Base(DeclarativeBase):
    pass


class Listing(Base):
    __tablename__ = "listings"
    id: Mapped[int] = mapped_column(primary_key=True)
    dedup_key: Mapped[str] = mapped_column(String, unique=True)
    data: Mapped[dict] = mapped_column(JSON)
    original_data: Mapped[dict] = mapped_column(JSON)
    first_seen: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)


class SearchPattern(Base):
    __tablename__ = "search_patterns"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    phrase: Mapped[str] = mapped_column(String, unique=True)
    original_phrase: Mapped[str] = mapped_column(String)
    canonical_concept: Mapped[str] = mapped_column(String)
    anomaly_category: Mapped[str] = mapped_column(String)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(default=True)
    priority_weight: Mapped[int] = mapped_column(default=50)
    appearances: Mapped[int] = mapped_column(default=0)
    useful_matches: Mapped[int] = mapped_column(default=0)
    successful_purchases: Mapped[int] = mapped_column(default=0)
    realized_profit: Mapped[str] = mapped_column(String, default="0")


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))
    action: Mapped[str] = mapped_column(String)
    note: Mapped[str] = mapped_column(Text, default="")
    realized_profit: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)


class Setting(Base):
    __tablename__ = "settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


def database(url=None):
    url = url or os.environ.get("RADAR_DATABASE_URL", "sqlite:///./data/radar.db")
    if url.startswith("sqlite:///") and url != "sqlite:///:memory:":
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return engine, sessionmaker(engine, expire_on_commit=False)

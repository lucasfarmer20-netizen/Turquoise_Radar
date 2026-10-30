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
    review_state: Mapped[str] = mapped_column(String, default="NEW")
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


class ComparableSale(Base):
    __tablename__ = "comparable_sales"
    id: Mapped[int] = mapped_column(primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)


class ListingEdit(Base):
    __tablename__ = "listing_edits"
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))
    before_data: Mapped[dict] = mapped_column(JSON)
    after_data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)


def database(url=None):
    url = url or os.environ.get("RADAR_DATABASE_URL", "sqlite:///./data/radar.db")
    if url.startswith("sqlite:///") and url != "sqlite:///:memory:":
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={"check_same_thread": False})
    from radar.migrations import migrate

    migrate(engine)
    return engine, sessionmaker(engine, expire_on_commit=False)


class DiscoveryGroup(Base):
    __tablename__ = "discovery_groups"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


class DiscoveryRun(Base):
    __tablename__ = "discovery_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="RUNNING")
    options: Mapped[dict] = mapped_column(JSON)
    started_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    new_count: Mapped[int] = mapped_column(default=0)
    duplicate_count: Mapped[int] = mapped_column(default=0)
    invalid_count: Mapped[int] = mapped_column(default=0)
    errors: Mapped[list] = mapped_column(JSON, default=list)


class SearchExecution(Base):
    __tablename__ = "search_executions"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("discovery_runs.id"))
    source: Mapped[str] = mapped_column(String)
    mode: Mapped[str] = mapped_column(String)
    group_ids: Mapped[list] = mapped_column(JSON)
    pattern_id: Mapped[str | None] = mapped_column(ForeignKey("search_patterns.id"))
    original_query: Mapped[str] = mapped_column(String)
    transmitted_query: Mapped[str] = mapped_column(String)
    corrected_queries: Mapped[list] = mapped_column(JSON, default=list)
    correction_state: Mapped[str] = mapped_column(String, default="UNKNOWN")
    parameters: Mapped[dict] = mapped_column(JSON)
    results_returned: Mapped[int] = mapped_column(default=0)
    unique_listings: Mapped[int] = mapped_column(default=0)
    new_count: Mapped[int] = mapped_column(default=0)
    duplicate_count: Mapped[int] = mapped_column(default=0)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String, default="RUNNING")
    executed_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)


class DiscoveredItem(Base):
    __tablename__ = "discovered_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    identity: Mapped[str] = mapped_column(String, unique=True)
    source: Mapped[str] = mapped_column(String)
    ebay_item_id: Mapped[str] = mapped_column(String)
    normalized: Mapped[dict] = mapped_column(JSON)
    original_data: Mapped[dict] = mapped_column(JSON)
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"))
    first_seen: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)


class DiscoveryObservation(Base):
    __tablename__ = "discovery_observations"
    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(ForeignKey("search_executions.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("discovered_items.id"))
    raw_data: Mapped[dict] = mapped_column(JSON)
    corrected_query: Mapped[str | None] = mapped_column(String)
    correction_state: Mapped[str] = mapped_column(String)
    observed_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)

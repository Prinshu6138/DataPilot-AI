import os
from datetime import datetime, timezone
from sqlalchemy import create_engine, event, ForeignKey, String, LargeBinary, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(d):
    return d.isoformat(timespec='seconds')+'Z'


url = os.getenv('DATABASE_URL', 'sqlite:///./datapilot.db')
if url.startswith(('postgres://','postgresql://')):
    url = 'postgresql+psycopg://' + url.split('://',1)[1]
engine = create_engine(url, pool_pre_ping=True, connect_args={'check_same_thread':False} if url.startswith('sqlite') else {})
if url.startswith('sqlite'):
    @event.listens_for(engine, 'connect')
    def sqlite_config(conn, _):
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA journal_mode=WAL')
Session = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase): pass


class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password: Mapped[str] = mapped_column(String(255))
    demo: Mapped[bool] = mapped_column(default=False)
    created: Mapped[datetime] = mapped_column(default=now)


class Login(Base):
    __tablename__ = 'sessions'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    expires: Mapped[datetime] = mapped_column(index=True)


class Dataset(Base):
    __tablename__ = 'datasets'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    name: Mapped[str] = mapped_column(String(180))
    original: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    quality: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(default=0)
    created: Mapped[datetime] = mapped_column(default=now)


class Run(Base):
    __tablename__ = 'analysis_history'
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id', ondelete='CASCADE'), index=True)
    created: Mapped[datetime] = mapped_column(default=now)
    action: Mapped[str] = mapped_column(String(60))
    findings: Mapped[int]


class Revision(Base):
    __tablename__ = 'cleaning_history'
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id', ondelete='CASCADE'), index=True)
    previous: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    details: Mapped[dict] = mapped_column(JSON)
    undone: Mapped[bool] = mapped_column(default=False)
    created: Mapped[datetime] = mapped_column(default=now)


class Report(Base):
    __tablename__ = 'generated_reports'
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey('datasets.id', ondelete='SET NULL'), index=True)
    name: Mapped[str] = mapped_column(String(220))
    summary: Mapped[dict] = mapped_column(JSON)
    pdf: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    docx: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    created: Mapped[datetime] = mapped_column(default=now)

"""ORM models.

Only the SHA-256 hash of the one-time token is stored, never the raw
token. Message text is stored as plain text in SQLite (this is a
local/self-hosted, non-sensitive-text tool; see README threat model).
"""
from __future__ import annotations

import datetime

from sqlalchemy import Boolean, DateTime, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils import utcnow


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(), default=utcnow, nullable=False)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(), nullable=False)
    viewed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(), nullable=True)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

"""Business logic for one-time messages.

Design note on "reveal" flow:
We intentionally do NOT mark a message as consumed on the first GET of
``/r/<token>``. Many things fetch links automatically without user intent:
link-preview unfurlers in messaging apps, antivirus/security scanners,
browser prefetch, and crawlers. If we consumed on first GET, a legitimate
recipient could find their one-time link already burned before they ever
saw it. Instead, the GET only validates the token and renders a page with
a "Reveal text" button. The subsequent POST to the reveal endpoint
atomically checks-and-consumes the message (in a single UPDATE statement
guarded by ``consumed = false``) and only then returns the text. This
keeps the "view exactly once" guarantee while being robust to
non-human first requests.
"""
from __future__ import annotations

import datetime
from pathlib import Path

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Message
from app.security import generate_token, hash_token
from app.utils import utcnow

ALLOWED_EXPIRY_MINUTES = (5, 10, 30)
DEFAULT_EXPIRY_MINUTES = 10


class MessageError(ValueError):
    """Raised for invalid message creation input."""


def validate_text(text: str) -> str:
    if text is None:
        raise MessageError("Text is required.")
    if len(text) == 0:
        raise MessageError("Text is required.")
    if len(text) > settings.max_message_length:
        raise MessageError(f"Text must be at most {settings.max_message_length} characters.")
    return text


def validate_file(filename: str | None, content_type: str | None, data: bytes | None) -> tuple[str, str, bytes]:
    if not filename or data is None:
        raise MessageError("A file is required.")
    if len(data) == 0:
        raise MessageError("The uploaded file is empty.")
    if len(data) > settings.max_upload_bytes:
        raise MessageError(f"Files must be at most {settings.max_upload_bytes // (1024 * 1024)} MiB.")

    safe_name = Path(filename).name.replace("\x00", "").strip()
    if not safe_name:
        raise MessageError("The uploaded file needs a valid name.")
    return safe_name[:255], (content_type or "application/octet-stream")[:255], data


def validate_expiry_minutes(minutes: int) -> int:
    if minutes not in ALLOWED_EXPIRY_MINUTES:
        raise MessageError("Invalid expiry selection.")
    return minutes


def create_message(
    db: Session,
    text: str,
    expiry_minutes: int,
    filename: str | None = None,
    content_type: str | None = None,
    file_data: bytes | None = None,
    is_live_note: bool = False,
) -> tuple[Message, str]:
    """Create a message, returning (record, raw_token).

    The raw token is never persisted; only its SHA-256 hash is stored.
    """
    expiry_minutes = validate_expiry_minutes(expiry_minutes)
    has_file = filename is not None or file_data is not None
    if is_live_note and has_file:
        raise MessageError("Live shared notes cannot include a file.")
    if has_file:
        if text.strip():
            raise MessageError("Share either text or one file, not both.")
        filename, content_type, file_data = validate_file(filename, content_type, file_data)
        text = ""
    else:
        text = validate_text(text)

    raw_token = generate_token()
    token_hash = hash_token(raw_token)
    now = utcnow()
    message = Message(
        token_hash=token_hash,
        text=text,
        file_name=filename,
        file_content_type=content_type,
        file_data=file_data,
        created_at=now,
        expires_at=now + datetime.timedelta(minutes=expiry_minutes),
        viewed_at=None,
        consumed=False,
        is_live_note=is_live_note,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message, raw_token


def get_valid_message(db: Session, raw_token: str) -> Message | None:
    """Look up a message by raw token, returning it only if it is still
    valid (exists, not expired, not consumed). Does not mutate state.
    """
    token_hash = hash_token(raw_token)
    message = db.query(Message).filter(Message.token_hash == token_hash).one_or_none()
    if message is None:
        return None
    if message.consumed:
        return None
    if message.expires_at <= utcnow():
        return None
    return message


def reveal_and_consume(db: Session, raw_token: str) -> Message | None:
    """Atomically validate and consume a message, returning it if it was
    successfully consumed just now (i.e. this caller "won" the race).
    """
    token_hash = hash_token(raw_token)
    now = utcnow()

    result = db.execute(
        update(Message)
        .where(
            Message.token_hash == token_hash,
            Message.consumed.is_(False),
            Message.expires_at > now,
        )
        .values(consumed=True, viewed_at=now)
    )
    db.commit()

    if result.rowcount != 1:
        return None

    message = db.query(Message).filter(Message.token_hash == token_hash).one_or_none()
    return message


def delete_message(db: Session, raw_token: str) -> bool:
    """Permanently delete a message row (used by "Done and delete now").

    Returns True if a row was deleted, False if the token did not
    correspond to any existing row.
    """
    token_hash = hash_token(raw_token)
    result = db.execute(delete(Message).where(Message.token_hash == token_hash))
    db.commit()
    return result.rowcount == 1


def cleanup_expired_and_consumed(db: Session) -> int:
    """Delete all messages that are expired or already consumed.

    Returns the number of rows deleted.
    """
    now = utcnow()
    result = db.execute(
        delete(Message).where((Message.expires_at <= now) | (Message.consumed.is_(True)))
    )
    db.commit()
    return result.rowcount or 0

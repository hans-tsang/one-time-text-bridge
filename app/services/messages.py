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


def validate_expiry_minutes(minutes: int) -> int:
    if minutes not in ALLOWED_EXPIRY_MINUTES:
        raise MessageError("Invalid expiry selection.")
    return minutes


def create_message(db: Session, text: str, expiry_minutes: int) -> tuple[Message, str]:
    """Create a message, returning (record, raw_token).

    The raw token is never persisted; only its SHA-256 hash is stored.
    """
    text = validate_text(text)
    expiry_minutes = validate_expiry_minutes(expiry_minutes)

    raw_token = generate_token()
    token_hash = hash_token(raw_token)
    now = utcnow()
    message = Message(
        token_hash=token_hash,
        text=text,
        created_at=now,
        expires_at=now + datetime.timedelta(minutes=expiry_minutes),
        viewed_at=None,
        consumed=False,
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

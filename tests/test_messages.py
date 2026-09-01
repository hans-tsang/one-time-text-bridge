import datetime

import pytest

from app.database import SessionLocal
from app.services.messages import (
    MessageError,
    cleanup_expired_and_consumed,
    create_message,
    delete_message,
    get_valid_message,
    reveal_and_consume,
)


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.close()


def test_create_message_stores_only_hash_not_raw_token(db):
    message, raw_token = create_message(db, text="hello world", expiry_minutes=10)
    assert message.token_hash != raw_token
    from app.security import hash_token

    assert message.token_hash == hash_token(raw_token)


def test_create_message_rejects_too_long_text(db):
    long_text = "a" * 2001
    with pytest.raises(MessageError):
        create_message(db, text=long_text, expiry_minutes=10)


def test_create_message_rejects_empty_text(db):
    with pytest.raises(MessageError):
        create_message(db, text="", expiry_minutes=10)


def test_create_message_accepts_max_length_text(db):
    text = "a" * 2000
    message, raw_token = create_message(db, text=text, expiry_minutes=10)
    assert message.text == text


def test_create_message_rejects_invalid_expiry(db):
    with pytest.raises(MessageError):
        create_message(db, text="hi", expiry_minutes=999)


def test_get_valid_message_returns_none_for_unknown_token(db):
    assert get_valid_message(db, "does-not-exist") is None


def test_get_valid_message_returns_message_for_valid_token(db):
    message, raw_token = create_message(db, text="hi", expiry_minutes=10)
    found = get_valid_message(db, raw_token)
    assert found is not None
    assert found.text == "hi"


def test_get_valid_message_rejects_expired_link(db):
    message, raw_token = create_message(db, text="hi", expiry_minutes=10)
    message.expires_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
    db.commit()
    assert get_valid_message(db, raw_token) is None


def test_reveal_and_consume_marks_message_consumed(db):
    message, raw_token = create_message(db, text="secret", expiry_minutes=10)
    revealed = reveal_and_consume(db, raw_token)
    assert revealed is not None
    assert revealed.text == "secret"
    assert revealed.consumed is True
    assert revealed.viewed_at is not None


def test_reveal_and_consume_is_one_time_only(db):
    message, raw_token = create_message(db, text="secret", expiry_minutes=10)
    first = reveal_and_consume(db, raw_token)
    second = reveal_and_consume(db, raw_token)
    assert first is not None
    assert second is None


def test_reveal_and_consume_rejects_expired(db):
    message, raw_token = create_message(db, text="secret", expiry_minutes=10)
    message.expires_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
    db.commit()
    assert reveal_and_consume(db, raw_token) is None


def test_delete_message_removes_row(db):
    message, raw_token = create_message(db, text="secret", expiry_minutes=10)
    delete_message(db, raw_token)
    assert get_valid_message(db, raw_token) is None


def test_cleanup_removes_expired_and_consumed(db):
    _, expired_token = create_message(db, text="a", expiry_minutes=10)
    expired = get_valid_message(db, expired_token)
    expired.expires_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
    db.commit()

    _, consumed_token = create_message(db, text="b", expiry_minutes=10)
    reveal_and_consume(db, consumed_token)

    _, active_token = create_message(db, text="c", expiry_minutes=10)

    deleted = cleanup_expired_and_consumed(db)
    assert deleted == 2
    assert get_valid_message(db, active_token) is not None

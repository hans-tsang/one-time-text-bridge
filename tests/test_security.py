from app.security import generate_token, hash_token


def test_generate_token_is_url_safe_and_long_enough():
    token = generate_token()
    assert isinstance(token, str)
    # 256 bits base64url-encoded is at least 43 chars
    assert len(token) >= 43


def test_generate_token_is_random():
    tokens = {generate_token() for _ in range(50)}
    assert len(tokens) == 50


def test_hash_token_is_sha256_hex():
    token = "abc123"
    digest = hash_token(token)
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_hash_token_is_deterministic():
    token = generate_token()
    assert hash_token(token) == hash_token(token)


def test_hash_token_differs_for_different_tokens():
    assert hash_token("a") != hash_token("b")

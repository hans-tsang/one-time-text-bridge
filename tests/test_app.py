import re

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _get_csrf(response) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match, "csrf token not found in response"
    return match.group(1)


def test_healthz_returns_ok_json():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_index_page_loads():
    response = client.get("/")
    assert response.status_code == 200
    assert "One-Time Text Bridge" in response.text


def test_security_headers_present_on_response():
    response = client.get("/")
    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in response.headers
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "Permissions-Policy" in response.headers


def test_create_form_loads():
    response = client.get("/create")
    assert response.status_code == 200
    assert "Create secure link" in response.text


def test_create_requires_consent_checkbox():
    get_response = client.get("/create")
    csrf = _get_csrf(get_response)
    response = client.post(
        "/create",
        data={"text": "hello", "expiry_minutes": "10", "csrf_token": csrf},
    )
    assert response.status_code == 400
    assert "must confirm" in response.text


def test_create_rejects_text_over_max_length():
    get_response = client.get("/create")
    csrf = _get_csrf(get_response)
    response = client.post(
        "/create",
        data={"text": "a" * 2001, "expiry_minutes": "10", "consent": "on", "csrf_token": csrf},
    )
    assert response.status_code == 400
    assert "2000" in response.text


def test_create_rejects_invalid_csrf_token():
    response = client.post(
        "/create",
        data={"text": "hello", "expiry_minutes": "10", "consent": "on", "csrf_token": "not-a-real-token"},
    )
    assert response.status_code == 400


def test_full_create_and_receive_flow():
    get_response = client.get("/create")
    csrf = _get_csrf(get_response)
    create_response = client.post(
        "/create",
        data={"text": "hello world", "expiry_minutes": "10", "consent": "on", "csrf_token": csrf},
    )
    assert create_response.status_code == 200
    assert "receive-url" in create_response.text

    match = re.search(r'value="(http://testserver/r/[^"]+)"', create_response.text)
    assert match
    receive_url = match.group(1)
    receive_path = receive_url.replace("http://testserver", "")

    receive_get = client.get(receive_path)
    assert receive_get.status_code == 200
    assert "Reveal text" in receive_get.text
    assert "hello world" not in receive_get.text

    reveal_csrf = _get_csrf(receive_get)
    reveal_response = client.post(f"{receive_path}/reveal", data={"csrf_token": reveal_csrf})
    assert reveal_response.status_code == 200
    assert "hello world" in reveal_response.text

    second_get = client.get(receive_path)
    assert second_get.status_code == 404
    assert "unavailable" in second_get.text.lower()


def test_text_share_ignores_empty_file_field():
    get_response = client.get("/create")
    csrf = _get_csrf(get_response)
    response = client.post(
        "/create",
        data={"text": "text only", "expiry_minutes": "10", "consent": "on", "csrf_token": csrf},
        files={"upload": ("", b"", "application/octet-stream")},
    )
    assert response.status_code == 200
    assert "receive-url" in response.text


def test_live_note_updates_connected_clients():
    get_response = client.get("/create")
    csrf = _get_csrf(get_response)
    create_response = client.post(
        "/create",
        data={
            "text": "initial",
            "share_mode": "live",
            "expiry_minutes": "10",
            "consent": "on",
            "csrf_token": csrf,
        },
    )
    receive_path = re.search(r'value="(http://testserver/live/[^\"]+)"', create_response.text).group(1)
    receive_path = receive_path.replace("http://testserver", "")
    assert client.get(receive_path).status_code == 200

    with client.websocket_connect(f"{receive_path}/ws") as first, client.websocket_connect(
        f"{receive_path}/ws"
    ) as second:
        assert first.receive_json() == {"type": "sync", "text": "initial"}
        assert second.receive_json() == {"type": "sync", "text": "initial"}
        first.send_json({"text": "now shared"})
        assert first.receive_json() == {"type": "update", "text": "now shared"}
        assert second.receive_json() == {"type": "update", "text": "now shared"}


def test_urls_include_configured_root_path():
    original_root_path = app.root_path
    app.root_path = "/one-time"
    try:
        response = client.get("/create")
        assert 'action="/one-time/create"' in response.text
        assert 'href="/one-time/"' in response.text
        assert 'href="/one-time/static/css/app.css"' in response.text
    finally:
        app.root_path = original_root_path


def test_receive_unknown_token_is_generic_unavailable():
    response = client.get("/r/this-token-does-not-exist")
    assert response.status_code == 404
    assert "unavailable" in response.text.lower()


def test_delete_message_makes_link_unavailable():
    get_response = client.get("/create")
    csrf = _get_csrf(get_response)
    create_response = client.post(
        "/create",
        data={"text": "delete me", "expiry_minutes": "10", "consent": "on", "csrf_token": csrf},
    )
    match = re.search(r'value="(http://testserver/r/[^"]+)"', create_response.text)
    receive_path = match.group(1).replace("http://testserver", "")

    receive_get = client.get(receive_path)
    delete_csrf = _get_csrf(receive_get)
    delete_response = client.post(f"{receive_path}/delete", data={"csrf_token": delete_csrf})
    assert delete_response.status_code == 200
    assert "Deleted" in delete_response.text

    second_get = client.get(receive_path)
    assert second_get.status_code == 404


def test_file_share_downloads_once():
    get_response = client.get("/create")
    csrf = _get_csrf(get_response)
    create_response = client.post(
        "/create",
        data={"text": "", "expiry_minutes": "10", "consent": "on", "csrf_token": csrf},
        files={"upload": ("photo.png", b"png data", "image/png")},
    )
    assert create_response.status_code == 200
    receive_path = re.search(r'value="(http://testserver/r/[^"]+)"', create_response.text).group(1)
    receive_path = receive_path.replace("http://testserver", "")

    receive_get = client.get(receive_path)
    assert receive_get.status_code == 200
    assert "Download photo.png" in receive_get.text

    reveal_csrf = _get_csrf(receive_get)
    download_response = client.post(f"{receive_path}/reveal", data={"csrf_token": reveal_csrf})
    assert download_response.status_code == 200
    assert download_response.content == b"png data"
    assert download_response.headers["content-type"] == "image/png"
    assert download_response.headers["content-disposition"] == 'attachment; filename="photo.png"'

    second_get = client.get(receive_path)
    assert second_get.status_code == 404

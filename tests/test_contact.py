"""Contact form (plan 3.1): persist first, send after, spam controls, confirmation gating."""

import smtplib

import httpx
import pytest
import respx
from pydantic import SecretStr

from src.core.config import settings
from src.models.contact_message_model import ContactMessageStatus
from src.services import contact_service as contact_service_module
from src.services.turnstile_service import TURNSTILE_VERIFY_URL
from tests import TEST_ENV

URL = "/api/v1/contact"
SUCCESS = {"success": True, "message": "Thanks! Your message is on its way.", "data": None}
PAYLOAD = {"name": "Visitor", "email": "visitor@example.com", "message": "Hello there"}


@pytest.fixture
def turnstile_on(monkeypatch):
    monkeypatch.setattr(settings, "turnstile_secret", SecretStr("turnstile-test-secret"))


@pytest.fixture
def confirmation_on(monkeypatch):
    monkeypatch.setattr(settings, "contact_confirmation_enabled", True)
    monkeypatch.setattr(contact_service_module, "_warned_confirmation_disabled", False)


def _only_message(repo):
    assert len(repo.messages) == 1
    return next(iter(repo.messages.values()))


# --- happy path and failure recording ---------------------------------------------


def test_message_is_stored_then_emailed(client, fake_contact_repo, fake_sender):
    response = client.post(URL, json=PAYLOAD)

    assert response.status_code == 202
    assert response.json() == SUCCESS
    stored = _only_message(fake_contact_repo)
    assert stored.status == ContactMessageStatus.SENT
    assert (stored.name, stored.email, stored.message) == (
        "Visitor",
        "visitor@example.com",
        "Hello there",
    )
    assert [mail["recipient"] for mail in fake_sender.sent] == [TEST_ENV["SUPER_ADMIN_EMAIL"]]


def test_row_is_pending_while_the_email_is_being_sent(client, fake_contact_repo, fake_sender):
    seen_status = []

    def recording_send(recipient, subject, html):
        seen_status.append(_only_message(fake_contact_repo).status)

    fake_sender.send_email = recording_send
    client.post(URL, json=PAYLOAD)

    assert seen_status == [ContactMessageStatus.PENDING]
    assert _only_message(fake_contact_repo).status == ContactMessageStatus.SENT


def test_smtp_failure_marks_the_row_failed(client, fake_contact_repo, fake_sender):
    fake_sender.fail_with = smtplib.SMTPAuthenticationError(535, b"bad credentials")

    response = client.post(URL, json=PAYLOAD)

    # The visitor's message is safe in the database even though the email failed.
    assert response.status_code == 202
    stored = _only_message(fake_contact_repo)
    assert stored.status == ContactMessageStatus.FAILED
    assert stored.error.startswith("SMTPAuthenticationError")


def test_email_html_escapes_visitor_input(client, fake_sender):
    client.post(URL, json={**PAYLOAD, "message": '<a href="https://evil.test">click</a>'})

    html = fake_sender.sent[0]["html"]
    assert '<a href="https://evil.test">' not in html
    assert "&lt;a href=&quot;https://evil.test&quot;&gt;" in html


def test_avatar_is_omitted_unless_configured(client, fake_sender, monkeypatch):
    client.post(URL, json=PAYLOAD)
    assert "<img" not in fake_sender.sent[0]["html"]

    monkeypatch.setattr(settings, "email_avatar_url", "https://cdn.example.com/me.png")
    client.post(URL, json=PAYLOAD)
    assert '<img src="https://cdn.example.com/me.png"' in fake_sender.sent[1]["html"]


# --- honeypot ----------------------------------------------------------------------


def test_honeypot_gets_success_but_nothing_is_stored_or_sent(
    client, fake_contact_repo, fake_sender
):
    response = client.post(URL, json={**PAYLOAD, "website": "https://spam.example"})

    assert response.status_code == 202
    assert response.json() == SUCCESS
    assert fake_contact_repo.messages == {}
    assert fake_sender.sent == []


# --- validation --------------------------------------------------------------------


@pytest.mark.parametrize(
    "override",
    [
        {"name": "n" * 101},
        {"name": "   "},
        {"message": "m" * 1001},
        {"message": ""},
        {"email": "not-an-email"},
        {"email": f"{'a' * 64}@{'b' * 63}.{'c' * 63}.{'d' * 60}.com"},
        {"unexpected": "field"},
    ],
)
def test_invalid_payloads_are_rejected(client, fake_contact_repo, override):
    response = client.post(URL, json={**PAYLOAD, **override})

    assert response.status_code == 422
    assert response.json()["success"] is False
    assert fake_contact_repo.messages == {}


def test_whitespace_is_trimmed(client, fake_contact_repo):
    client.post(URL, json={**PAYLOAD, "name": "  Visitor  ", "message": "  Hi  "})

    stored = _only_message(fake_contact_repo)
    assert (stored.name, stored.message) == ("Visitor", "Hi")


# --- Turnstile ---------------------------------------------------------------------


def test_turnstile_not_required_without_a_secret(client, fake_contact_repo):
    with respx.mock(assert_all_called=False) as mock:
        verify = mock.post(TURNSTILE_VERIFY_URL)
        response = client.post(URL, json=PAYLOAD)

    assert response.status_code == 202
    assert not verify.called
    assert len(fake_contact_repo.messages) == 1


def test_turnstile_missing_token_is_rejected(client, fake_contact_repo, turnstile_on):
    response = client.post(URL, json=PAYLOAD)

    assert response.status_code == 400
    assert response.json()["message"] == "Verification failed. Please try again."
    assert fake_contact_repo.messages == {}


def test_turnstile_invalid_token_is_rejected(client, fake_contact_repo, turnstile_on):
    with respx.mock as mock:
        mock.post(TURNSTILE_VERIFY_URL).mock(
            return_value=httpx.Response(
                200, json={"success": False, "error-codes": ["invalid-input-response"]}
            )
        )
        response = client.post(URL, json={**PAYLOAD, "turnstile_token": "bad"})

    assert response.status_code == 400
    assert fake_contact_repo.messages == {}


def test_turnstile_valid_token_is_accepted_and_sent_to_cloudflare(
    client, fake_contact_repo, turnstile_on
):
    with respx.mock as mock:
        verify = mock.post(TURNSTILE_VERIFY_URL).mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        response = client.post(URL, json={**PAYLOAD, "turnstile_token": "good-token"})

    assert response.status_code == 202
    assert len(fake_contact_repo.messages) == 1
    form = dict(httpx.QueryParams(verify.calls.last.request.content.decode()))
    assert form["secret"] == "turnstile-test-secret"
    assert form["response"] == "good-token"
    assert form["remoteip"] == "testclient"


def test_turnstile_outage_fails_closed(client, fake_contact_repo, turnstile_on):
    with respx.mock as mock:
        mock.post(TURNSTILE_VERIFY_URL).mock(side_effect=httpx.ConnectError("down"))
        response = client.post(URL, json={**PAYLOAD, "turnstile_token": "good-token"})

    assert response.status_code == 400
    assert fake_contact_repo.messages == {}


# --- confirmation email ------------------------------------------------------------


def _verified_post(client, payload):
    with respx.mock as mock:
        mock.post(TURNSTILE_VERIFY_URL).mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        return client.post(URL, json={**payload, "turnstile_token": "good-token"})


def test_confirmation_sent_when_enabled_with_turnstile(
    client, fake_sender, turnstile_on, confirmation_on
):
    _verified_post(client, {**PAYLOAD, "message": "my secret project details"})

    recipients = [mail["recipient"] for mail in fake_sender.sent]
    assert recipients == [TEST_ENV["SUPER_ADMIN_EMAIL"], "visitor@example.com"]
    confirmation = fake_sender.sent[1]
    assert "my secret project details" not in confirmation["html"]
    assert "Hi Visitor" in confirmation["html"]


def test_no_confirmation_without_turnstile_even_if_enabled(client, fake_sender, confirmation_on):
    client.post(URL, json=PAYLOAD)

    assert [mail["recipient"] for mail in fake_sender.sent] == [TEST_ENV["SUPER_ADMIN_EMAIL"]]


def test_no_confirmation_when_disabled(client, fake_sender, turnstile_on):
    _verified_post(client, PAYLOAD)

    assert [mail["recipient"] for mail in fake_sender.sent] == [TEST_ENV["SUPER_ADMIN_EMAIL"]]


def test_no_confirmation_when_the_owner_email_failed(
    client, fake_sender, turnstile_on, confirmation_on
):
    fake_sender.fail_with = smtplib.SMTPServerDisconnected("gone")

    _verified_post(client, PAYLOAD)

    assert fake_sender.sent == []

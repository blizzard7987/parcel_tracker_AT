"""Gmail with a Google sign-in (gmail.readonly) instead of an app password."""

import base64
import time
from datetime import UTC, datetime
from email.utils import format_datetime
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import aiohttp
import pytest
from aioresponses import aioresponses
from homeassistant.components.application_credentials import (
    ClientCredential,
    async_import_client_credential,
)
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.parcel_tracker.application_credentials import (
    GOOGLE_AUTHORIZE_URL,
    GOOGLE_TOKEN_URL,
    async_get_authorization_server,
)
from custom_components.parcel_tracker.const import DOMAIN
from custom_components.parcel_tracker.coordinator import ParcelCoordinator
from custom_components.parcel_tracker.mail import readonly
from custom_components.parcel_tracker.mail.gmail_api import (
    GMAIL_API,
    GMAIL_SCOPE,
    GmailApiClient,
)
from custom_components.parcel_tracker.mail.imap import ImapAuthError, ImapUnavailable
from custom_components.parcel_tracker.store import ParcelStore

SETUP = "custom_components.parcel_tracker.async_setup_entry"
LIST = f"{GMAIL_API}/messages"


def _raw(i: int) -> bytes:
    return (
        "From: Shop <shop@example.org>\r\n"
        f"Message-ID: <g{i}@example.org>\r\n"
        "Subject: Versandbestaetigung\r\n"
        f"Date: {format_datetime(datetime.now(UTC))}\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        f"DHL-Sendungsnummer: 0034099999999999991{i}\r\n"
    ).encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class FakeOAuth:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.token = {"access_token": "at", "expires_at": time.time() + 3600}
        self.checks = 0

    async def async_ensure_token_valid(self) -> None:
        self.checks += 1
        if self.error:
            raise self.error


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as s:
        yield s


async def test_fetch_lists_by_query_and_returns_raw_oldest_first(session):
    client = GmailApiClient(FakeOAuth(), session)
    with aioresponses() as m:
        m.get(
            f"{LIST}?maxResults=100&q={readonly.gmail_query(readonly.INITIAL_DAYS)}",
            payload={"messages": [{"id": "b"}, {"id": "a"}]},
        )
        m.get(f"{LIST}/a?format=raw", payload={"id": "a", "raw": _b64(_raw(1))})
        m.get(f"{LIST}/b?format=raw", payload={"id": "b", "raw": _b64(_raw(2))})
        mails = await client.async_fetch_unseen()
        sent = [c for calls in m.requests.values() for c in calls]
    assert [uid for uid, _ in mails] == ["a", "b"]  # the API lists newest first
    assert mails[0][1] == _raw(1)
    assert all(c.kwargs["headers"]["Authorization"] == "Bearer at" for c in sent)


async def test_second_fetch_skips_seen_ids_and_uses_short_window(session):
    client = GmailApiClient(FakeOAuth(), session)
    with aioresponses() as m:
        m.get(
            f"{LIST}?maxResults=100&q={readonly.gmail_query(readonly.INITIAL_DAYS)}",
            payload={"messages": [{"id": "a"}]},
        )
        m.get(f"{LIST}/a?format=raw", payload={"raw": _b64(_raw(1))})
        mails = await client.async_fetch_unseen()
        client.finish([(uid, None) for uid, _ in mails], True)
        m.get(
            f"{LIST}?maxResults=100&q={readonly.gmail_query(readonly.READ_ONLY_DAYS)}",
            payload={"messages": [{"id": "c"}, {"id": "a"}]},
        )
        m.get(f"{LIST}/c?format=raw", payload={"raw": _b64(_raw(3))})
        mails = await client.async_fetch_unseen()
    assert [uid for uid, _ in mails] == ["c"]


async def test_follows_pages(session):
    client = GmailApiClient(FakeOAuth(), session)
    q = readonly.gmail_query(readonly.INITIAL_DAYS)
    with aioresponses() as m:
        first = {"messages": [{"id": "b"}], "nextPageToken": "p2"}
        m.get(f"{LIST}?maxResults=100&q={q}", payload=first)
        m.get(f"{LIST}?maxResults=100&pageToken=p2&q={q}", payload={"messages": [{"id": "a"}]})
        m.get(f"{LIST}/a?format=raw", payload={"raw": _b64(_raw(1))})
        m.get(f"{LIST}/b?format=raw", payload={"raw": _b64(_raw(2))})
        mails = await client.async_fetch_unseen()
    assert [uid for uid, _ in mails] == ["a", "b"]


async def test_empty_result(session):
    client = GmailApiClient(FakeOAuth(), session)
    with aioresponses() as m:
        m.get(f"{LIST}?maxResults=100&q={readonly.gmail_query(readonly.INITIAL_DAYS)}", payload={})
        assert await client.async_fetch_unseen() == []


@pytest.mark.parametrize("status", [401, 403])
async def test_rejected_token_is_an_auth_error(session, status):
    client = GmailApiClient(FakeOAuth(), session)
    with aioresponses() as m:
        m.get(
            f"{LIST}?maxResults=100&q={readonly.gmail_query(readonly.INITIAL_DAYS)}",
            status=status,
        )
        with pytest.raises(ImapAuthError):
            await client.async_fetch_unseen()


async def test_server_error_is_unavailable(session):
    client = GmailApiClient(FakeOAuth(), session)
    with aioresponses() as m:
        m.get(
            f"{LIST}?maxResults=100&q={readonly.gmail_query(readonly.INITIAL_DAYS)}",
            status=503,
        )
        with pytest.raises(ImapUnavailable):
            await client.async_fetch_unseen()


async def test_revoked_refresh_token_is_an_auth_error(session):
    client = GmailApiClient(FakeOAuth(ConfigEntryAuthFailed("revoked")), session)
    with pytest.raises(ImapAuthError):
        await client.async_fetch_unseen()


async def test_refresh_without_network_is_unavailable(session):
    client = GmailApiClient(FakeOAuth(aiohttp.ClientConnectionError()), session)
    with pytest.raises(ImapUnavailable):
        await client.async_fetch_unseen()


async def test_never_writes(session):
    client = GmailApiClient(FakeOAuth(), session)
    assert client.read_only is True
    assert not hasattr(client, "purge_processed")
    client.finish([("a", "x")], True)
    assert client._looked_at == {"a"}


async def test_google_endpoints(hass):
    server = await async_get_authorization_server(hass)
    assert server.authorize_url == GOOGLE_AUTHORIZE_URL == "https://accounts.google.com/o/oauth2/v2/auth"
    assert server.token_url == GOOGLE_TOKEN_URL == "https://oauth2.googleapis.com/token"


class AsyncMailbox:
    gmail_api = True

    def __init__(self, mails=(), error=None):
        self.mails = list(mails)
        self.error = error
        self.finished = []

    async def async_fetch_unseen(self):
        if self.error:
            raise self.error
        mails, self.mails = self.mails, []
        return mails

    def finish(self, dispositions, move):
        self.finished.append(dispositions)


async def _coordinator(hass, mailbox):
    entry = MockConfigEntry(domain=DOMAIN, data={"postcode": "10115"})
    entry.add_to_hass(hass)
    store = ParcelStore(hass)
    await store.async_load()
    return ParcelCoordinator(hass, entry, store, {}, mailbox), store


async def test_coordinator_awaits_the_async_mailbox(hass):
    mailbox = AsyncMailbox([("a", _raw(1))])
    coord, store = await _coordinator(hass, mailbox)
    await coord.async_import_mail()
    assert len(store.parcels) == 1
    assert [uid for uid, _ in mailbox.finished[0]] == ["a"]


async def test_coordinator_auth_error_raises_gmail_issue(hass):
    coord, _ = await _coordinator(hass, AsyncMailbox(error=ImapAuthError("revoked")))
    await coord.async_import_mail()
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, "gmail_auth") is not None
    assert issues.async_get_issue(DOMAIN, "imap_auth") is None


# --- the flow -------------------------------------------------------------------


@pytest.fixture
async def credential(hass):
    assert await async_setup_component(hass, "application_credentials", {})
    await async_import_client_credential(
        hass, DOMAIN, ClientCredential("client-id", "client-secret"), "cred"
    )


async def _start(hass, entry):
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.MENU
    return result


async def test_reconfigure_menu_without_sign_in(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={"postcode": "1230"})
    entry.add_to_hass(hass)
    result = await _start(hass, entry)
    assert result["menu_options"] == ["gmail_connect"]


@pytest.mark.usefixtures("current_request_with_host", "credential")
async def test_connect_gmail_stores_token(hass, hass_client_no_auth, aioclient_mock):
    entry = MockConfigEntry(domain=DOMAIN, data={"postcode": "1230", "country": "at"})
    entry.add_to_hass(hass)
    result = await _start(hass, entry)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "gmail_connect"}
    )
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    query = parse_qs(urlparse(result["url"]).query)
    assert query["scope"] == [GMAIL_SCOPE]
    assert query["access_type"] == ["offline"]
    assert query["client_id"] == ["client-id"]
    client = await hass_client_no_auth()
    resp = await client.get(f"/auth/external/callback?code=abcd&state={query['state'][0]}")
    assert resp.status == 200
    aioclient_mock.post(
        GOOGLE_TOKEN_URL,
        json={
            "refresh_token": "rt",
            "access_token": "at",
            "type": "Bearer",
            "expires_in": 3599,
        },
    )
    aioclient_mock.get(f"{GMAIL_API}/profile", json={"emailAddress": "x@gmail.com"})
    with patch(SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["token"]["refresh_token"] == "rt"
    assert entry.data["auth_implementation"] == "cred"
    assert entry.data["postcode"] == "1230"  # the rest stays


@pytest.mark.usefixtures("current_request_with_host", "credential")
async def test_connect_without_gmail_access_stores_nothing(
    hass, hass_client_no_auth, aioclient_mock
):
    entry = MockConfigEntry(domain=DOMAIN, data={"postcode": "1230"})
    entry.add_to_hass(hass)
    result = await _start(hass, entry)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "gmail_connect"}
    )
    state = parse_qs(urlparse(result["url"]).query)["state"][0]
    client = await hass_client_no_auth()
    await client.get(f"/auth/external/callback?code=abcd&state={state}")
    aioclient_mock.post(
        GOOGLE_TOKEN_URL,
        json={"refresh_token": "rt", "access_token": "at", "expires_in": 3599},
    )
    aioclient_mock.get(f"{GMAIL_API}/profile", status=403)
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["reason"] == "gmail_no_access"
    assert "token" not in entry.data


async def test_disconnect_removes_the_sign_in(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"postcode": "1230", "token": {"access_token": "at"}, "auth_implementation": "cred"},
    )
    entry.add_to_hass(hass)
    result = await _start(hass, entry)
    assert result["menu_options"] == ["gmail_connect", "gmail_disconnect"]
    with patch(SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "gmail_disconnect"}
        )
    assert result["reason"] == "gmail_disconnected"
    assert entry.data == {"postcode": "1230"}


async def test_reauth_of_a_sign_in_asks_to_sign_in_again(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"token": {"access_token": "at"}, "auth_implementation": "cred"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "gmail_reauth"


async def test_reauth_without_sign_in_still_asks_for_the_dhl_key(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={"dhl_api_key": "old"})
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"


def test_scope_is_read_only():
    assert GMAIL_SCOPE == "https://www.googleapis.com/auth/gmail.readonly"

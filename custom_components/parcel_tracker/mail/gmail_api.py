"""Paket Tracker AT: read Gmail through the Gmail API with a read-only Google sign-in.

Instead of an app password (which opens the whole mailbox) the user signs in with
Google and grants only ``gmail.readonly``: Google itself guarantees that nothing is
changed, and the access can be revoked in the Google account at any time. The OAuth
client is the user's own (Home Assistant "Application Credentials"), so no third party
ever sees a token or a mail.

Same contract as the IMAP client in read-only mode: the same Gmail search
(``readonly.gmail_query``), a stock-taking of INITIAL_DAYS after a start, then
READ_ONLY_DAYS; messages are fetched as raw RFC 822 and handed to the same parsers.
Nothing is ever written to the mailbox.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Protocol

import aiohttp
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from .imap import FETCH_LIMIT, ImapAuthError, ImapUnavailable
from .readonly import INITIAL_DAYS, READ_ONLY_DAYS, gmail_query

_LOGGER = logging.getLogger(__name__)

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
_TIMEOUT = aiohttp.ClientTimeout(total=30)


class TokenSource(Protocol):
    """What the client needs of Home Assistant's OAuth2Session."""

    async def async_ensure_token_valid(self) -> None: ...

    @property
    def token(self) -> dict[str, Any]: ...


class GmailApiClient:
    """Fetches shipping mails via the Gmail API; never changes the mailbox."""

    read_only = True
    gmail_api = True

    def __init__(self, oauth: TokenSource, session: aiohttp.ClientSession) -> None:
        self._oauth = oauth
        self._session = session
        self._looked_at: set[str] = set()
        self._stock_taken = False

    async def _headers(self) -> dict[str, str]:
        try:
            await self._oauth.async_ensure_token_valid()
        except ConfigEntryAuthFailed as err:
            # Refresh token revoked or expired (HA starts the reauth flow itself).
            raise ImapAuthError("Google sign-in expired or revoked") from err
        except aiohttp.ClientResponseError as err:
            # Older Home Assistant: the refresh answer as is (400 invalid_grant, 401).
            if err.status in (400, 401):
                raise ImapAuthError("Google sign-in expired or revoked") from err
            raise ImapUnavailable(f"token refresh HTTP {err.status}") from err
        except (TimeoutError, aiohttp.ClientError, HomeAssistantError) as err:
            raise ImapUnavailable(type(err).__name__) from err
        return {"Authorization": f"Bearer {self._oauth.token['access_token']}"}

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        headers = await self._headers()
        try:
            async with self._session.get(
                f"{GMAIL_API}/{path}", params=params, headers=headers, timeout=_TIMEOUT
            ) as resp:
                if resp.status in (401, 403):
                    raise ImapAuthError(f"Gmail API HTTP {resp.status}")
                if resp.status != 200:
                    raise ImapUnavailable(f"Gmail API HTTP {resp.status}")
                data = await resp.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError) as err:
            raise ImapUnavailable(type(err).__name__) from err
        if not isinstance(data, dict):
            raise ImapUnavailable("Gmail API answered no object")
        return data

    async def async_fetch_unseen(self) -> list[tuple[str, bytes]]:
        """(id, raw message) of the shipping mails not looked at yet, oldest first."""
        days = READ_ONLY_DAYS if self._stock_taken else INITIAL_DAYS
        ids: list[str] = []
        token: str | None = None
        for _ in range(10):  # at most 10 pages of 100
            params = {"q": gmail_query(days), "maxResults": "100"}
            if token:
                params["pageToken"] = token
            page = await self._get("messages", params)
            listed = page.get("messages") or []
            ids += [m["id"] for m in listed if isinstance(m, dict) and m.get("id")]
            token = page.get("nextPageToken")
            if not token:
                break
        ids.reverse()  # the API lists newest first
        fresh = [i for i in ids if i not in self._looked_at][:FETCH_LIMIT]
        mails: list[tuple[str, bytes]] = []
        for message_id in fresh:
            data = await self._get(f"messages/{message_id}", {"format": "raw"})
            raw = data.get("raw")
            if isinstance(raw, str) and raw:
                mails.append((message_id, base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))))
        if len([i for i in ids if i not in self._looked_at]) <= FETCH_LIMIT:
            self._stock_taken = True
        return mails

    async def async_check(self) -> None:
        """One call that needs the grant (raises ImapAuthError / ImapUnavailable)."""
        await self._get("profile", {})

    def finish(self, dispositions: list[tuple[str, str | None]], move: bool) -> None:
        """Nothing is filed: the mails are only remembered as looked at."""
        self._looked_at.update(uid for uid, _ in dispositions)

    def filing_state(self) -> dict[str, Any]:
        return {"gmail_api": True}

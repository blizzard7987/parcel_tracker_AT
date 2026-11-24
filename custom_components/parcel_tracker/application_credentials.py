"""Paket Tracker AT: Google as OAuth server for the read-only Gmail access.

The user brings their own OAuth client (Settings > Application Credentials), as for
Home Assistant's own Google integrations; Paket Tracker never ships a client secret.
"""

from homeassistant.components.application_credentials import AuthorizationServer
from homeassistant.core import HomeAssistant

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


async def async_get_authorization_server(hass: HomeAssistant) -> AuthorizationServer:
    """Google's OAuth endpoints."""
    return AuthorizationServer(authorize_url=GOOGLE_AUTHORIZE_URL, token_url=GOOGLE_TOKEN_URL)


async def async_get_description_placeholders(hass: HomeAssistant) -> dict[str, str]:
    """Links for the dialog that asks for the OAuth client."""
    return {
        "oauth_consent_url": "https://console.cloud.google.com/apis/credentials/consent",
        "more_info_url": "https://github.com/blizzard7987/parcel_tracker_AT/blob/main/docs/mail-import.md#gmail-mit-google-anmeldung",
        "oauth_creds_url": "https://console.cloud.google.com/apis/credentials",
        "redirect_url": "https://my.home-assistant.io/redirect/oauth",
    }

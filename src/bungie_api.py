"""
Bungie API client for Destiny 2.

Provides authenticated HTTP access to the Bungie.net API endpoints
for querying player profiles, item definitions, vendors, and manifests.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests


class BungieAPIError(Exception):
    """Raised when the Bungie API returns a non-success response."""

    def __init__(
        self, message: str, error_code: int, error_status: str, response: requests.Response
    ) -> None:
        self.error_code = error_code
        self.error_status = error_status
        self.response = response
        super().__init__(message)


class BungieRateLimitError(BungieAPIError):
    """Raised when the Bungie API rate limit is exceeded."""

    def __init__(self, response: requests.Response) -> None:
        try:
            retry_after = int(response.headers.get("Retry-After", "0"))
        except (ValueError, TypeError):
            retry_after = 0
        self.retry_after = retry_after
        msg = (
            f"Rate limited by Bungie API. Retry-After: {retry_after}s. "
            f"Status: {response.status_code}"
        )
        super().__init__(msg, 0, "ThrottleLimitExceeded", response)


@dataclass
class BungieManifest:
    """Represents the Bungie.net manifest metadata."""

    version: str
    mobile_asset_content_path: str
    mobile_gear_asset_data_bases: list[dict[str, str]]
    mobile_gear_cdn: dict[str, str]
    mobile_world_content_paths: dict[str, str]
    json_world_content_paths: dict[str, str]
    json_world_component_paths: dict[str, str]
    mobile_clan_banner_database_path: str
    mobile_gear_cdn_path: str
    activity_asset_override_path: str | None = None
    image_paths: dict[str, str] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BungieManifest:
        return cls(
            version=data["version"],
            mobile_asset_content_path=data["mobileAssetContentPath"],
            mobile_gear_asset_data_bases=data.get("mobileGearAssetDataBases", []),
            mobile_gear_cdn=data.get("mobileGearCDN", {}),
            mobile_world_content_paths=data.get("mobileWorldContentPaths", {}),
            json_world_content_paths=data.get("jsonWorldContentPaths", {}),
            json_world_component_paths=data.get("jsonWorldComponentPaths", {}),
            mobile_clan_banner_database_path=data.get("mobileClanBannerDatabasePath", ""),
            mobile_gear_cdn_path=data.get("mobileGearCDNPath", ""),
            activity_asset_override_path=data.get("activityAssetOverridePath"),
            image_paths=data.get("imagePaths"),
        )


class BungieAPI:
    """Client for the Bungie.net public API.

    Handles authentication, request signing, rate limiting, and error
    handling for all Destiny 2 endpoints.

    Attributes:
        api_key: The Bungie.net API key used for all requests.
        oauth_token: Optional OAuth Bearer token for authenticated endpoints.
        session: ``requests.Session`` with pre-configured headers.
        base_url: The Bungie API root URL.
        _last_request_time: Timestamp of the most recent API call (rate limiting).
    """

    BASE_URL = "https://www.bungie.net/Platform"
    MAX_REQUESTS_PER_SECOND = 2
    MIN_INTERVAL = 1.0 / MAX_REQUESTS_PER_SECOND

    def __init__(
        self,
        api_key: str,
        oauth_token: str | None = None,
        refresh_token: str | None = None,
        oauth_client_id: str | None = None,
        oauth_client_secret: str | None = None,
    ) -> None:
        """Initialise the client with authentication credentials.

        Args:
            api_key: A valid Bungie.net API key.
            oauth_token: An optional OAuth Bearer token for scoped endpoints.
            refresh_token: OAuth refresh token for silent token renewal.
            oauth_client_id: Bungie app OAuth client ID (needed to refresh).
            oauth_client_secret: Bungie app OAuth client secret (needed to refresh).
        """
        self.api_key = api_key
        self.oauth_token = oauth_token
        self.refresh_token = refresh_token
        self.oauth_client_id = oauth_client_id
        self.oauth_client_secret = oauth_client_secret
        self.session = requests.Session()
        self.base_url = self.BASE_URL

        self._rebuild_headers()
        self._last_request_time: float = 0.0

    def _rebuild_headers(self) -> None:
        """Re-build session headers after a token refresh."""
        headers: dict[str, str] = {
            "X-API-Key": self.api_key,
            "Accept": "application/json",
        }
        if self.oauth_token:
            headers["Authorization"] = f"Bearer {self.oauth_token}"
        self.session.headers.clear()
        self.session.headers.update(headers)

    def _refresh_oauth(self) -> bool:
        """Try to refresh the OAuth token using the stored refresh token.

        Returns True if the token was refreshed successfully.
        """
        if not self.refresh_token or not self.oauth_client_id:
            return False

        try:
            resp = requests.post(
                "https://www.bungie.net/Platform/App/OAuth/Token/",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                    "client_id": self.oauth_client_id,
                    "client_secret": self.oauth_client_secret or "",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            self.oauth_token = data.get("access_token", self.oauth_token)
            if data.get("refresh_token"):
                self.refresh_token = data["refresh_token"]
            self._rebuild_headers()
            return True
        except Exception:
            return False

    def _rate_limit(self) -> None:
        """Enforce a maximum request rate by sleeping if needed."""
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self.MIN_INTERVAL:
            time.sleep(self.MIN_INTERVAL - elapsed)
        self._last_request_time = time.monotonic()

    def _request(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Core request method with error handling and OAuth auto-refresh.

        If a request fails with a 404 (common when OAuth token is expired)
        and a refresh token is available, it attempts one silent refresh
        and retries the request once.
        """
        self._rate_limit()
        url = f"{self.base_url}{path}"
        response = self.session.get(url, params=params)

        # Attempt OAuth refresh on 404 if we have a refresh token
        if response.status_code == 404 and self.refresh_token:
            if self._refresh_oauth():
                # Retry once with the new token
                self._rate_limit()
                response = self.session.get(url, params=params)
        response.raise_for_status()  # raises for non-2xx HTTP status codes
        payload: dict[str, Any] = response.json()

        # The Bungie API wraps the actual data in a 'Response' envelope.
        bungie_response = payload.get("Response")
        error_code: int = payload.get("ErrorCode", 1)
        error_status: str = payload.get("ErrorStatus", "Success")
        message: str = payload.get("Message", "")

        if error_code != 1:
            raise BungieAPIError(
                f"Bungie API error {error_code} ({error_status}): {message}",
                error_code=error_code,
                error_status=error_status,
                response=response,
            )

        return bungie_response  # type: ignore[return-value]

    def search_destiny_player(
        self,
        display_name: str,
        membership_type: int = -1,
    ) -> list[dict[str, Any]]:
        """Search for a Destiny player by display name.

        Args:
            display_name: The player's Bungie display name (formerly gamertag).
            membership_type: Platform type (-1 for all, 1=Xbox, 2=PSN,
                3=Steam, 4=Blizzard, 5=Stadia, 6=Epic, 10=Twitter,
                254=All, etc.).

        Returns:
            A list of player info dictionaries, each containing
            ``membershipType``, ``membershipId``, ``displayName``, etc.
        """
        path = f"/Destiny2/SearchDestinyPlayer/{membership_type}/{display_name}/"
        return self._request(path)

    def get_profile(
        self,
        membership_type: int,
        membership_id: int,
        components: list[int] | None = None,
    ) -> dict[str, Any]:
        """Retrieve a Destiny 2 player's full profile.

        Args:
            membership_type: The platform membership type (e.g. 3 for Steam).
            membership_id: The player's platform-specific membership ID.
            components: List of component IDs to include in the response.
                Defaults to a comprehensive set covering profiles, characters,
                inventory, equipment, item instances/stats/sockets/plugs.

        Returns:
            Profile data dictionary keyed by component type.
        """
        if components is None:
            components = [
                100, 200, 201, 205, 300, 302, 304, 307, 308,
            ]
        params = {"components": ",".join(str(c) for c in components)}
        path = f"/Destiny2/{membership_type}/Profile/{membership_id}/"
        return self._request(path, params=params)

    def get_item(
        self,
        membership_type: int,
        membership_id: int,
        item_instance_id: int,
        components: list[int] | None = None,
    ) -> dict[str, Any]:
        """Get a single Destiny 2 inventory item by instance ID.

        Args:
            membership_type: The platform membership type.
            membership_id: The player's membership ID.
            item_instance_id: The unique instance ID of the item.
            components: Component IDs to include. Defaults to instances,
                stats, sockets, and plug states.

        Returns:
            Item data dictionary keyed by component type.
        """
        if components is None:
            components = [300, 302, 304, 307]
        params = {"components": ",".join(str(c) for c in components)}
        path = (
            f"/Destiny2/{membership_type}/Profile/{membership_id}/"
            f"Item/{item_instance_id}/"
        )
        return self._request(path, params=params)

    def get_manifest(self) -> BungieManifest:
        """Retrieve the Destiny 2 manifest metadata.

        Returns:
            A ``BungieManifest`` dataclass with paths and version info
            used to download world content databases.
        """
        data = self._request("/Destiny2/Manifest/")
        return BungieManifest.from_dict(data)

    def get_entity_definition(
        self, entity_type: str, hash_val: int
    ) -> dict[str, Any]:
        """Retrieve a single Destiny 2 entity definition by hash.

        Args:
            entity_type: The type of entity (e.g. ``DestinyInventoryItemDefinition``,
                ``DestinyClassDefinition``, ``DestinyStatDefinition``, etc.).
            hash_val: The 32-bit hash of the desired definition.

        Returns:
            The entity definition as a dictionary.
        """
        path = f"/Destiny2/Manifest/{entity_type}/{hash_val}/"
        return self._request(path)

    def get_vendor_items(
        self,
        membership_type: int,
        membership_id: int,
        character_id: int,
        vendor_hash: int,
    ) -> dict[str, Any]:
        """Get items sold by a specific vendor for a character.

        Args:
            membership_type: The platform membership type.
            membership_id: The player's membership ID.
            character_id: The character ID to query.
            vendor_hash: The hash identifier for the vendor definition.

        Returns:
            Vendor sales data including categories, item lists, and costs.
        """
        path = (
            f"/Destiny2/{membership_type}/Profile/{membership_id}/"
            f"Character/{character_id}/Vendor/{vendor_hash}/"
        )
        return self._request(path)

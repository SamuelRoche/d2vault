"""
Manifest Cache Module
=====================
Handles downloading the Bungie manifest SQLite database and querying it for
item / perk / stat definitions.

Usage
-----
    mc = ManifestCache("my_api_key")
    if mc.needs_update():
        mc.download_manifest(progress_callback=lambda p, s: print(f"{p:.0%} - {s}"))
    item = mc.get_item_definition(123456)
    mc.close()
"""

from __future__ import annotations

import gzip
import json
import os
import sqlite3
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional

BUNGIE_BASE = "https://www.bungie.net"
MANIFEST_URL = (
    "https://www.bungie.net/Platform/Destiny2/Manifest/"
)

CACHE_DIR = Path.home() / ".destiny_manifest"
VERSION_FILE = CACHE_DIR / "version.txt"
DB_PATH = CACHE_DIR / "manifest.sqlite"
DB_GZ_PATH = CACHE_DIR / "manifest.sqlite.gz"


class ManifestError(Exception):
    """Base exception for manifest-related errors."""


class ManifestDownloadError(ManifestError):
    """Raised when downloading the manifest fails."""


class ManifestQueryError(ManifestError):
    """Raised when querying the manifest database fails."""


class ManifestCache:
    """Download, cache, and query the Destiny 2 manifest SQLite database."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self.api_key = api_key
        self._conn: Optional[sqlite3.Connection] = None
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Version management
    # ------------------------------------------------------------------

    def get_manifest_version(self) -> Optional[str]:
        """Fetch the current manifest version from the Bungie API.

        Returns None if the request fails (network / auth).
        """
        req = urllib.request.Request(MANIFEST_URL)
        req.add_header("X-API-Key", self.api_key)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise ManifestDownloadError(
                f"Failed to fetch manifest version from API: {exc}"
            ) from exc

        if data.get("ErrorCode") != 1:
            raise ManifestDownloadError(
                f"Bungie API returned ErrorCode {data.get('ErrorCode')}: "
                f"{data.get('ErrorStatus', 'unknown')}"
            )

        response = data.get("Response", {})
        return response.get("version")

    def get_cached_version(self) -> Optional[str]:
        """Read the locally cached manifest version.

        Returns None when no version file exists (first run).
        """
        if VERSION_FILE.exists():
            return VERSION_FILE.read_text().strip()
        return None

    def needs_update(self) -> bool:
        """Compare the remote version against the cached version.

        Returns True when:
          - there is no cached version, OR
          - the local DB file is missing, OR
          - the remote version differs from the cached one.
        """
        if not DB_PATH.exists():
            return True

        try:
            remote = self.get_manifest_version()
        except ManifestDownloadError:
            # If we can't contact the API, assume current is fine
            return False

        if remote is None:
            return False

        local = self.get_cached_version()
        return remote != local

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download_manifest(
        self, progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> None:
        """Download the latest manifest SQLite database from Bungie.

        Parameters
        ----------
        progress_callback:
            Called with ``(progress_fraction, status_message)`` during
            download and extraction.  ``progress_fraction`` is a float in
            [0.0, 1.0].
        """
        # 1. Get the manifest metadata
        if progress_callback:
            progress_callback(0.0, "Fetching manifest metadata…")

        req = urllib.request.Request(MANIFEST_URL)
        req.add_header("X-API-Key", self.api_key)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                manifest_data = json.loads(resp.read().decode())
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise ManifestDownloadError(
                f"Failed to fetch manifest metadata: {exc}"
            ) from exc

        if manifest_data.get("ErrorCode") != 1:
            raise ManifestDownloadError(
                f"Bungie API returned ErrorCode {manifest_data.get('ErrorCode')}: "
                f"{manifest_data.get('ErrorStatus', 'unknown')}"
            )

        response = manifest_data.get("Response", {})
        db_rel_path = response.get("mobileWorldContentPaths", {}).get("en")
        if not db_rel_path:
            raise ManifestDownloadError(
                "No 'mobileWorldContentPaths[\"en\"]' in manifest response"
            )

        version = response.get("version", "unknown")

        download_url = f"{BUNGIE_BASE}{db_rel_path}"

        # 2. Download the gzipped SQLite DB
        if progress_callback:
            progress_callback(0.05, f"Downloading manifest v{version}…")

        try:
            gz_path = str(DB_GZ_PATH)
            urllib.request.urlretrieve(download_url, gz_path)
        except urllib.error.URLError as exc:
            raise ManifestDownloadError(
                f"Failed to download manifest from {download_url}: {exc}"
            ) from exc

        if progress_callback:
            progress_callback(0.7, "Decompressing manifest database…")

        # 3. Decompress (Bungie sometimes ships gzip, sometimes zip)
        raw_path = DB_GZ_PATH
        raw_bytes = raw_path.read_bytes()

        if raw_bytes[:2] == b'\x1f\x8b':
            # Gzip format
            if progress_callback:
                progress_callback(0.7, "Decompressing gzip manifest…")
            try:
                with gzip.open(raw_path, "rb") as f_in:
                    with open(DB_PATH, "wb") as f_out:
                        while True:
                            chunk = f_in.read(1024 * 1024)
                            if not chunk:
                                break
                            f_out.write(chunk)
            except OSError as exc:
                raise ManifestDownloadError(
                    f"Failed to decompress gzip manifest: {exc}"
                ) from exc
        elif raw_bytes[:2] == b'PK':
            # Zip format
            if progress_callback:
                progress_callback(0.7, "Decompressing zip manifest…")
            try:
                import zipfile
                with zipfile.ZipFile(raw_path) as zf:
                    # Find the SQLite file inside the zip
                    names = zf.namelist()
                    sqlite_files = [n for n in names if n.endswith('.sqlite') or n.endswith('.content') or n.endswith('.db')]
                    if sqlite_files:
                        target = sqlite_files[0]
                    else:
                        target = names[0]
                    if progress_callback:
                        progress_callback(0.75, f"Extracting {target}…")
                    with zf.open(target) as f_in:
                        with open(DB_PATH, "wb") as f_out:
                            while True:
                                chunk = f_in.read(1024 * 1024)
                                if not chunk:
                                    break
                                f_out.write(chunk)
                # Remove the zip after extraction
                raw_path.unlink(missing_ok=True)
                raw_path = Path()  # reset so we don't try to unlink again
            except Exception as exc:
                raise ManifestDownloadError(
                    f"Failed to extract zip manifest: {exc}"
                ) from exc
        else:
            # Assume it's already a raw SQLite
            raw_path.rename(DB_PATH)

        # 4. Clean up the .gz file
        DB_GZ_PATH.unlink(missing_ok=True)

        if progress_callback:
            progress_callback(0.9, "Verifying database…")

        # 5. Quick integrity check
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute("SELECT COUNT(*) FROM sqlite_master")
            conn.close()
        except sqlite3.DatabaseError as exc:
            DB_PATH.unlink(missing_ok=True)
            raise ManifestDownloadError(
                f"Downloaded manifest is not a valid SQLite database: {exc}"
            ) from exc

        # 6. Cache the version
        VERSION_FILE.write_text(version)

        if progress_callback:
            progress_callback(1.0, "Manifest ready.")

    # ------------------------------------------------------------------
    # Database connection helpers
    # ------------------------------------------------------------------

    @property
    def conn(self) -> sqlite3.Connection:
        """Lazy-initialised connection to the local manifest DB."""
        if self._conn is None:
            if not DB_PATH.exists():
                raise ManifestError(
                    "No manifest database found. Call download_manifest() first."
                )
            self._conn = sqlite3.connect(str(DB_PATH))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _query_single(
        self, table: str, hash_value: int, hash_column: str = "id"
    ) -> Optional[dict]:
        """Fetch a single row from *table* by *hash_column*.

        Returns the parsed JSON from the ``json`` column, or None if not
        found.  Most Bungie definition tables store their payload in a
        ``json`` column.
        """
        sql = f"SELECT json FROM {table} WHERE {hash_column} = ?"
        try:
            row = self.conn.execute(sql, (hash_value,)).fetchone()
        except sqlite3.DatabaseError as exc:
            raise ManifestQueryError(
                f"Error querying {table} for hash {hash_value}: {exc}"
            ) from exc

        if row is None:
            return None
        try:
            return json.loads(row["json"])
        except (json.JSONDecodeError, TypeError) as exc:
            raise ManifestQueryError(
                f"Invalid JSON in {table} row for hash {hash_value}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Definition lookups
    # ------------------------------------------------------------------

    def get_item_definition(self, item_hash: int) -> Optional[dict]:
        """Return the item definition for *item_hash* (DestinyInventoryItemDefinition)."""
        return self._query_single("DestinyInventoryItemDefinition", item_hash)

    def get_inventory_item_definition(self, item_hash: int) -> Optional[dict]:
        """Alias for :meth:`get_item_definition`."""
        return self.get_item_definition(item_hash)

    def get_sandbox_perk_definition(self, perk_hash: int) -> Optional[dict]:
        """Return the sandbox perk definition for *perk_hash*.

        This looks up ``DestinySandboxPerkDefinition``.
        """
        return self._query_single("DestinySandboxPerkDefinition", perk_hash)

    def get_stat_definition(self, stat_hash: int) -> Optional[dict]:
        """Return the stat definition for *stat_hash*.

        This looks up ``DestinyStatDefinition``.
        """
        return self._query_single("DestinyStatDefinition", stat_hash)

    def search_items_by_name(self, name_pattern: str) -> list[dict]:
        """LIKE-search item definitions whose ``itemName`` or ``itemTypeDisplayName``
        contains *name_pattern*.

        This is a simple SQL ``LIKE`` query against the JSON column; it
        is not intended for production-scale fuzzy search.
        """
        like = f"%{name_pattern}%"
        sql = """
            SELECT json
            FROM DestinyInventoryItemDefinition
            WHERE json LIKE ?
        """
        try:
            rows = self.conn.execute(sql, (like,)).fetchall()
        except sqlite3.DatabaseError as exc:
            raise ManifestQueryError(
                f"Error searching items by name '{name_pattern}': {exc}"
            ) from exc

        results: list[dict] = []
        for row in rows:
            try:
                defn = json.loads(row["json"])
            except (json.JSONDecodeError, TypeError):
                continue
            # Only include entries that look like real items
            name = defn.get("displayProperties", {}).get("name", "")
            if name and name_pattern.lower() in name.lower():
                results.append(defn)

        return results

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the SQLite connection if it is open."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "ManifestCache":
        return self

    def __exit__(self, *exc_details) -> None:
        self.close()

"""
Vault Reader Module
===================
Reads a Destiny 2 player's vault and character inventories via the Bungie API.

Provides functions to:
  - Fetch all items (vault + characters) from a player profile
  - Resolve item hashes to human-readable names and perk names via ManifestCache
  - Return structured item dictionaries filtered to legendary+ tier items

Usage
-----
    reader = VaultReader(api_client, manifest_cache)
    items = reader.read_vault(membership_type=3, membership_id=123456)
    # or use the convenience function:
    all_items = collect_all_inventories(api_client, 3, 123456, manifest_cache)
"""

from __future__ import annotations

from typing import Any

from .bungie_api import BungieAPI
from .manifest_cache import ManifestCache

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minimum tier to keep (5 = Legendary, 6 = Exotic, etc.)
MIN_TIER = 5

# Bucket type hashes for categorisation
BUCKET_VAULT = 138197509  # Vault (ProfileInventory)
BUCKET_WEAPONS = 1498876634  # Kinetic / Energy / Power weapons
BUCKET_ARMOR = 1423942364  # Helmet / Gauntlets / Chest / Legs / Class item
BUCKET_GENERAL = 1107761855  # General / consumables / materials

# Component IDs used when fetching the profile
# Some components require OAuth (201, 302, 307, 308)
PUBLIC_COMPONENTS = [
    100,  # Profiles
    200,  # Characters
    205,  # CharacterEquipment
    300,  # ItemInstances
    304,  # ItemSockets
]

OAUTH_COMPONENTS = [
    201,  # CharacterInventory
    302,  # ItemStats
    307,  # ItemPlugStates
    308,  # ItemPlugObjectives
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ITEM_CATEGORY_WEAPON = 1
_ITEM_CATEGORY_ARMOR = 20
_ITEM_CATEGORY_GHOST = 39
_ITEM_CATEGORY_VEHICLE = 34
_ITEM_CATEGORY_SHIP = 24
_ITEM_CATEGORY_EMBLEM = 23
_ITEM_CATEGORY_FINISHER = 40
_ITEM_CATEGORY_MOD = 56
_ITEM_CATEGORY_SHADER = 41


def _item_tier(defn: dict[str, Any] | None) -> int:
    """Return the tier type (int) from an item definition, defaulting to 0."""
    if not defn:
        return 0
    inventory = defn.get("inventory", {})
    return inventory.get("tierType", 0)


def _is_weapon(defn: dict[str, Any] | None) -> bool:
    """Return True if the item definition represents a weapon."""
    if not defn:
        return False
    categories = defn.get("itemCategoryHashes", [])
    return _ITEM_CATEGORY_WEAPON in categories


def _is_armor(defn: dict[str, Any] | None) -> bool:
    """Return True if the item definition represents an armour piece."""
    if not defn:
        return False
    categories = defn.get("itemCategoryHashes", [])
    return _ITEM_CATEGORY_ARMOR in categories


def _item_slot(defn: dict[str, Any] | None) -> str:
    """Return the display name for the item's bucket/slot."""
    if not defn:
        return "Unknown"
    inv = defn.get("inventory", {})
    bucket_hash = inv.get("bucketTypeHash", 0)
    # Map known bucket hashes to human-readable names
    bucket_names: dict[int, str] = {
        1498876634: "Kinetic",
        2465295065: "Energy",
        953998645: "Power",
        3448274439: "Helmet",
        3555269338: "Gauntlets",
        1423942364: "Chest",
        20886954: "Legs",
        1585787867: "Class Item",
        4023194814: "Ghost",
        284967655: "Ship",
        1400270851: "Sparrow",
        375726501: "Clan Banner",
        4274335291: "Emblem",
        2025709151: "Shader",
        138197509: "Vault",
        1107761855: "Inventory",
    }
    return bucket_names.get(bucket_hash, f"Bucket_{bucket_hash}")


def _item_type_name(defn: dict[str, Any] | None) -> str:
    """Return the human-readable item type name (e.g. 'Auto Rifle', 'Helmet')."""
    if not defn:
        return "Unknown"
    return defn.get("itemTypeDisplayName", "Unknown")


def _get_perk_name(
    manifest_cache: ManifestCache, plug_hash: int
) -> str:
    """Resolve a plug hash to a human-readable perk name.

    Tries multiple definition tables in order:
      1. DestinySandboxPerkDefinition
      2. DestinyInventoryItemDefinition (for mods / intrinsics)
    """
    # Try sandbox perk first
    defn = manifest_cache.get_sandbox_perk_definition(plug_hash)
    if defn:
        dp = defn.get("displayProperties", {})
        name = dp.get("name")
        if name:
            return name

    # Fall back to inventory item definition (covers mods, adept mods, etc.)
    defn = manifest_cache.get_item_definition(plug_hash)
    if defn:
        dp = defn.get("displayProperties", {})
        name = dp.get("name")
        if name:
            return name

    return f"UnknownPerk_{plug_hash}"


def _get_stat_name(manifest_cache: ManifestCache, stat_hash: int) -> str:
    """Resolve a stat hash to a human-readable name."""
    defn = manifest_cache.get_stat_definition(stat_hash)
    if defn:
        dp = defn.get("displayProperties", {})
        name = dp.get("name")
        if name:
            return name
    return f"Stat_{stat_hash}"


# ---------------------------------------------------------------------------
# VaultReader
# ---------------------------------------------------------------------------


class VaultReader:
    """Reads a Destiny 2 player's vault and character inventories.

    Parameters
    ----------
    api_client: BungieAPI
        Authenticated Bungie API client.
    manifest_cache: ManifestCache
        Manifest cache for resolving hashes to human-readable names.
    """

    def __init__(
        self, api_client: BungieAPI, manifest_cache: ManifestCache
    ) -> None:
        self.api = api_client
        self.manifest = manifest_cache

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read_vault(
        self,
        membership_type: int,
        membership_id: int,
    ) -> list[dict[str, Any]]:
        """Read vault + all character inventories.

        Returns a list of structured item dictionaries (only legendary+
        tier items).

        Parameters
        ----------
        membership_type:
            The platform membership type (e.g. 3 for Steam).
        membership_id:
            The player's platform-specific membership ID.

        Returns
        -------
        list[dict]
            Each dict has keys: item_hash, instance_id, name, type, slot,
            tier, is_weapon, is_armor, perks, stats, masterwork, location,
            bucket_hash.
        """
        # Use full components (including OAuth-required) if we have a token,
        # otherwise fall back to public-only components (no vault reading).
        if self.api.oauth_token:
            components = PUBLIC_COMPONENTS + OAUTH_COMPONENTS
        else:
            components = PUBLIC_COMPONENTS
            print("  [no OAuth token — vault won't be available, only equipped gear]")

        profile = self.api.get_profile(
            membership_type,
            membership_id,
            components=components,
        )

        items: list[dict[str, Any]] = []

        # --- 1. Vault items (profile inventory, component 201) ---
        profile_inventory = profile.get("profileInventory", {}).get(
            "data", {}
        )
        vault_items = profile_inventory.get("items", [])
        for item in vault_items:
            parsed = self._parse_item(item, location="vault")
            if parsed:
                items.append(parsed)

        # --- 2. Character inventories & equipment ---
        characters = profile.get("characterInventories", {})
        equipment = profile.get("characterEquipment", {})

        for char_id_str, char_data in characters.get("data", {}).items():
            char_items = char_data.get("items", [])
            for item in char_items:
                parsed = self._parse_item(
                    item, location=f"character_{char_id_str}"
                )
                if parsed:
                    items.append(parsed)

        for char_id_str, equip_data in equipment.get("data", {}).items():
            equip_items = equip_data.get("items", [])
            for item in equip_items:
                parsed = self._parse_item(
                    item, location=f"character_{char_id_str}"
                )
                if parsed:
                    items.append(parsed)

        return items

    # ------------------------------------------------------------------
    # Internal: parse a single item dictionary
    # ------------------------------------------------------------------

    def _parse_item(
        self,
        item: dict[str, Any],
        location: str,
    ) -> dict[str, Any] | None:
        """Parse a single item from the profile response.

        Returns a structured dict, or None if the item should be filtered
        (below legendary tier).
        """
        item_hash: int = item.get("itemHash", 0)
        instance_id: int | None = item.get("itemInstanceId")
        bucket_hash: int = item.get("bucketHash", 0)
        quantity: int = item.get("quantity", 1)

        # Skip placeholder / zero-hash items
        if not item_hash:
            return None

        # Get definition & filter by tier
        defn = self.manifest.get_item_definition(item_hash)
        tier = _item_tier(defn)
        if tier < MIN_TIER:
            return None

        # Basic info
        dp = (defn or {}).get("displayProperties", {})
        name: str = dp.get("name", "Unknown")
        item_type: str = _item_type_name(defn)
        slot: str = _item_slot(defn)
        is_weapon: bool = _is_weapon(defn)
        is_armor: bool = _is_armor(defn)

        # Build the result dictionary
        result: dict[str, Any] = {
            "item_hash": item_hash,
            "instance_id": instance_id,
            "name": name,
            "type": item_type,
            "slot": slot,
            "tier": tier,
            "is_weapon": is_weapon,
            "is_armor": is_armor,
            "bucket_hash": bucket_hash,
            "quantity": quantity,
            "location": location,
            "perks": [],
            "stats": {},
            "masterwork": None,
        }

        # Non-instanced items (e.g. consumables, currencies) have no
        # instance-specific data (perks, stats, masterwork). They are
        # still returned for completeness.
        if not instance_id:
            return result

        # Instance-specific enrichment (perks, stats, masterwork) is
        # handled externally via _enrich_item_from_components after
        # the full profile is fetched.

        return result

    def _enrich_item_from_components(
        self,
        result: dict[str, Any],
        instance_data: dict[str, Any] | None,
        stats_data: dict[str, Any] | None,
        sockets_data: dict[str, Any] | None,
        plug_states: dict[str, Any] | None,
    ) -> None:
        """Enrich a partial item dict with instance/stat/socket data.

        Modifies *result* in place.
        """
        if not result.get("instance_id"):
            return

        instance_id_str = str(result["instance_id"])

        # ---- Instance (component 300): masterwork, etc. ----
        instance_info: dict[str, Any] | None = None
        if instance_data:
            instance_info = instance_data.get(instance_id_str)
        if not instance_info:
            instance_info = {}

        # Check for masterwork
        damage_type = instance_info.get("damageTypeHash")
        # Masterwork info is stored in item stats, not a direct field.
        # We'll detect it via the stat "statHash" == 4188031367 (masterwork)
        # or via the item definition's investment stats.

        # ---- Stats (component 302) ----
        item_stats: dict[str, Any] = {}
        if stats_data:
            stat_entries = stats_data.get(instance_id_str, {}).get(
                "stats", {}
            )
            for stat_hash_str, stat_info in stat_entries.items():
                try:
                    stat_hash = int(stat_hash_str)
                except (ValueError, TypeError):
                    continue
                stat_value: int | None = stat_info.get("value")
                if stat_value is None:
                    continue
                stat_name = _get_stat_name(self.manifest, stat_hash)
                item_stats[stat_name] = stat_value

                # Detect masterwork stat: hash 4188031367 == "Masterwork"
                if stat_hash == 4188031367:
                    result["masterwork"] = {
                        "stat_name": stat_name,
                        "value": stat_value,
                    }
        result["stats"] = item_stats

        # ---- Sockets & plug states (components 304 & 307) ----
        perks: list[dict[str, Any]] = []

        # Build a lookup of plug state by socket index
        plug_state_map: dict[int, dict[str, Any]] = {}
        if plug_states:
            socket_entries = plug_states.get(instance_id_str, {}).get(
                "reusablePlugs", {}
            )
            for socket_idx_str, plugs in socket_entries.items():
                try:
                    socket_idx = int(socket_idx_str)
                except (ValueError, TypeError):
                    continue
                # The last (selected) plug in the list is the active one
                if plugs:
                    plug_state_map[socket_idx] = plugs[-1]

        # Also check plugObjectives for override plugs
        plug_objective_map: dict[int, dict[str, Any]] = {}

        if sockets_data:
            socket_entries = sockets_data.get(instance_id_str, {}).get(
                "sockets", []
            )
            for socket_entry in socket_entries:
                socket_idx: int = socket_entry.get("socketIndex", 0)
                # The active plug hash:
                plug_hash: int | None = socket_entry.get("plugHash")
                if not plug_hash:
                    continue

                # Check if reusablePlugs override this
                override_plug = plug_state_map.get(socket_idx, {})

                # Determine if the plug is activated (equipped)
                is_activated: bool = socket_entry.get("isActivated", True)

                plug_name = _get_perk_name(self.manifest, plug_hash)

                perk_entry: dict[str, Any] = {
                    "socket_index": socket_idx,
                    "plug_hash": plug_hash,
                    "plug_name": plug_name,
                    "is_activated": is_activated,
                }
                perks.append(perk_entry)

                # If reusable plug state overrides the name
                if override_plug and override_plug.get("plugItemHash"):
                    override_hash = override_plug["plugItemHash"]
                    override_name = _get_perk_name(
                        self.manifest, override_hash
                    )
                    perk_entry["plug_name"] = override_name
                    perk_entry["plug_hash"] = override_hash

        result["perks"] = perks

    def _collect_item_components(
        self, profile: dict[str, Any]
    ) -> tuple[
        dict[str, Any] | None,
        dict[str, Any] | None,
        dict[str, Any] | None,
        dict[str, Any] | None,
        dict[str, Any] | None,
    ]:
        """Extract instance, stat, socket, plug state, and plug objective
        data from the profile response.

        Returns a tuple of (instance_data, stats_data, sockets_data,
        plug_states, plug_objectives) — each is the dict keyed by
        instance ID, or None.
        """
        item_components = profile.get("itemComponents", {})

        instances = item_components.get("instances", {}).get("data")
        stats = item_components.get("stats", {}).get("data")
        sockets = item_components.get("sockets", {}).get("data")
        plug_states = item_components.get("plugStates", {}).get("data")
        plug_objectives = item_components.get("plugObjectives", {}).get(
            "data"
        )

        return instances, stats, sockets, plug_states, plug_objectives

    def read_vault_with_components(
        self,
        membership_type: int,
        membership_id: int,
    ) -> list[dict[str, Any]]:
        """Full read: vault + character items with perks, stats, masterwork.

        This is the recommended entry point for production use.

        Returns a list of structured item dicts with all fields populated.
        """
        profile = self.api.get_profile(
            membership_type,
            membership_id,
            components=PROFILE_COMPONENTS,
        )

        instances, stats, sockets, plug_states, plug_objectives = (
            self._collect_item_components(profile)
        )

        items: list[dict[str, Any]] = []

        # Helper to parse and enrich a raw item
        def _process(item_raw: dict[str, Any], loc: str) -> None:
            parsed = self._parse_item(item_raw, location=loc)
            if parsed:
                self._enrich_item_from_components(
                    parsed,
                    instances,
                    stats,
                    sockets,
                    plug_states,
                    plug_objectives,
                )
                items.append(parsed)

        # --- Vault ---
        profile_inventory = profile.get("profileInventory", {}).get(
            "data", {}
        )
        for item in profile_inventory.get("items", []):
            _process(item, "vault")

        # --- Character inventories ---
        char_inventories = profile.get("characterInventories", {}).get(
            "data", {}
        )
        for char_id_str, char_data in char_inventories.items():
            for item in char_data.get("items", []):
                _process(item, f"character_{char_id_str}")

        # --- Character equipment ---
        char_equipment = profile.get("characterEquipment", {}).get(
            "data", {}
        )
        for char_id_str, equip_data in char_equipment.items():
            for item in equip_data.get("items", []):
                _process(item, f"character_{char_id_str}")

        return items


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------


def collect_all_inventories(
    api_client: BungieAPI,
    membership_type: int,
    membership_id: int,
    manifest_cache: ManifestCache,
) -> list[dict[str, Any]]:
    """Unified convenience function to collect all vault + character items.

    This is the simplest way to get all items for a player in one call.

    Parameters
    ----------
    api_client:
        Authenticated ``BungieAPI`` instance.
    membership_type:
        Platform membership type (e.g. 3 for Steam).
    membership_id:
        Player's platform-specific membership ID.
    manifest_cache:
        ``ManifestCache`` instance (must have a downloaded manifest).

    Returns
    -------
    list[dict]
        Structured item dictionaries with perk, stat, and masterwork data.
    """
    reader = VaultReader(api_client, manifest_cache)
    return reader.read_vault_with_components(membership_type, membership_id)

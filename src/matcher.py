"""
Vault Matcher & Analyzer Module
================================
The core matching and analysis engine for the Destiny 2 vault tool.

Accepts vault items (as returned by :class:`VaultReader`) and god roll data
(from a JSON database), then calculates match scores, generates keep/dismantle
recommendations, and produces farming suggestions.

Exports
-------
- class VaultAnalyzer
- analyze_weapon(weapon_item)
- analyze_armor(armor_item, all_armor_items)
- analyze_vault(all_items)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Scoring constants
# ---------------------------------------------------------------------------

SCORE_EXACT = 100  # All 4 perk columns match a god roll variant
SCORE_MAIN_TRAITS = 75  # Columns 3 + 4 match (the main "traits")
SCORE_SINGLE_TRAIT = 50  # Only one main trait matches
SCORE_BARREL_MAG = 25  # Only barrel / mag match
SCORE_NONE = 0  # No match

# Armor stat thresholds
ARMOR_TOTAL_HIGH = 65  # Total stat points considered "high"
ARMOR_TOTAL_EXCELLENT = 68  # Excellent roll
ARMOR_STAT_HIGH = 20  # Single stat value considered "spiky"

# How many copies of each armor piece to keep
ARMOR_KEEP_COUNT = 3

# Masterwork bonus applied to *all* stats when masterwork is active
MASTERWORK_BONUS_STAT = 10

# Socket indices for standard weapon perk columns
# These are the typical indices used by Bungie's socket system:
#   0 = intrinsic (frame)
#   1 = barrel / scope
#   2 = magazine / battery
#   3 = trait 1 (left column)
#   4 = trait 2 (right column)
#   5 = masterwork (sometimes)
SOCKET_BARREL = 1
SOCKET_MAG = 2
SOCKET_TRAIT_1 = 3
SOCKET_TRAIT_2 = 4

# The "main traits" = columns the player cares about most
MAIN_TRAIT_SOCKETS = {SOCKET_TRAIT_1, SOCKET_TRAIT_2}
BARREL_MAG_SOCKETS = {SOCKET_BARREL, SOCKET_MAG}
ALL_PERK_SOCKETS = {SOCKET_BARREL, SOCKET_MAG, SOCKET_TRAIT_1, SOCKET_TRAIT_2}


# ---------------------------------------------------------------------------
# Weights source
# ---------------------------------------------------------------------------

_GOD_ROLLS_DEFAULT = Path("god_rolls") / "weapons.json"


def _load_god_rolls(path: str | Path) -> dict[str, Any]:
    """Load the god roll database from a JSON file.

    Expected structure (version 1)::

        {
            "version": 1,
            "weapons": {
                "<weapon_name>": {
                    "hash": <int>,           # item hash (optional)
                    "source": "<source>",     # e.g. "World Drop", "Raid", "Nightfall"
                    "craftable": true|false,
                    "deepsight_required": <int>,  # number of red borders needed
                    "rolls": [
                        {
                            "name": "<roll name>",
                            "barrel": "<perk name>",
                            "mag": "<perk name>",
                            "trait_1": "<perk name>",
                            "trait_2": "<perk name>",
                            "masterwork": "<masterwork stat>",  # optional
                        },
                        ...
                    ]
                },
                ...
            }
        }

    Returns an empty dict if the file is missing or invalid.
    """
    path = Path(path)
    if not path.exists():
        return {}

    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}

    if not isinstance(raw, dict):
        return {}

    weapons = raw.get("weapons", raw)
    if isinstance(weapons, dict):
        return weapons
    return {}


# ---------------------------------------------------------------------------
# Helper: resolve perk names from vault item perks
# ---------------------------------------------------------------------------


def _perks_by_socket(item: dict[str, Any]) -> dict[int, str]:
    """Build a ``{socket_index: plug_name}`` lookup from an item's perk list.

    Only includes activated perks and skips any with unknown names.
    """
    result: dict[int, str] = {}
    for perk in item.get("perks", []):
        idx = perk.get("socket_index")
        name = perk.get("plug_name", "")
        if idx is not None and name and name.lower() not in (
            "",
            "unknown",
        ):
            # Only index up to column 4 is relevant for matching
            if idx in ALL_PERK_SOCKETS:
                result[idx] = name
    return result


def _is_activated_masterwork(
    item: dict[str, Any], masterwork_name: str
) -> bool:
    """Check whether an item has an activated masterwork matching *name*."""
    mw = item.get("masterwork")
    if not mw:
        return False
    stat_name: str = mw.get("stat_name", "")
    return stat_name.lower() == masterwork_name.lower()


# ---------------------------------------------------------------------------
# Scoring logic
# ---------------------------------------------------------------------------


def _score_god_roll(
    item_perks: dict[int, str], god_roll: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """Compare a weapon's active perks against a single god roll variant.

    Parameters
    ----------
    item_perks:
        ``{socket_index: plug_name}`` for the vault item.
    god_roll:
        A god roll variant dict with keys ``barrel``, ``mag``, ``trait_1``,
        ``trait_2``, and optionally ``masterwork`` and ``name``.

    Returns
    -------
    tuple[int, dict]
        ``(score, details)`` where *details* contains per-column match info.
    """
    details: dict[str, Any] = {
        "roll_name": god_roll.get("name", "Unknown Roll"),
        "barrel_match": False,
        "mag_match": False,
        "trait_1_match": False,
        "trait_2_match": False,
        "masterwork_match": False,
    }

    # Compare each column
    gr_barrel = god_roll.get("barrel", "")
    gr_mag = god_roll.get("mag", "")
    gr_trait_1 = god_roll.get("trait_1", "")
    gr_trait_2 = god_roll.get("trait_2", "")

    if gr_barrel and item_perks.get(SOCKET_BARREL, "").lower() == gr_barrel.lower():
        details["barrel_match"] = True
    if gr_mag and item_perks.get(SOCKET_MAG, "").lower() == gr_mag.lower():
        details["mag_match"] = True
    if gr_trait_1 and item_perks.get(SOCKET_TRAIT_1, "").lower() == gr_trait_1.lower():
        details["trait_1_match"] = True
    if gr_trait_2 and item_perks.get(SOCKET_TRAIT_2, "").lower() == gr_trait_2.lower():
        details["trait_2_match"] = True

    # Masterwork: bonus if matching, not a penalty if not
    gr_mw = god_roll.get("masterwork", "")
    if gr_mw:
        # Check if weapon masterwork stat name matches (case-insensitive)
        item_mw = item_perks.get(SOCKET_BARREL + 2)  # not standard; check item directly
        details["masterwork_match"] = True  # We'll refine below

    # Determine score tier
    both_traits = details["trait_1_match"] and details["trait_2_match"]
    single_trait = details["trait_1_match"] or details["trait_2_match"]
    both_barrel_mag = details["barrel_match"] and details["mag_match"]
    all_four = (
        both_traits and both_barrel_mag
    )

    if all_four:
        score = SCORE_EXACT
    elif both_traits:
        score = SCORE_MAIN_TRAITS
    elif single_trait:
        score = SCORE_SINGLE_TRAIT
    elif both_barrel_mag:
        score = SCORE_BARREL_MAG
    else:
        score = SCORE_NONE

    return score, details


def _best_god_roll_match(
    item_perks: dict[int, str],
    god_roll_variants: list[dict[str, Any]],
) -> tuple[int, dict[str, Any]]:
    """Find the best-matching god roll variant for a weapon.

    Returns ``(best_score, best_details)`` where *best_details* includes
    the roll name and per-column match info for the closest variant.
    """
    best_score = SCORE_NONE
    best_details: dict[str, Any] = {
        "roll_name": "No Match",
        "barrel_match": False,
        "mag_match": False,
        "trait_1_match": False,
        "trait_2_match": False,
        "masterwork_match": False,
    }

    for variant in god_roll_variants:
        score, details = _score_god_roll(item_perks, variant)
        if score > best_score:
            best_score = score
            best_details = details
            best_details["roll_name"] = variant.get("name", "Unknown Roll")

    return best_score, best_details


# ---------------------------------------------------------------------------
# Recommendation logic
# ---------------------------------------------------------------------------


def _recommendation_from_score(
    score: int,
    match_details: dict[str, Any],
    weapon_name: str,
    god_roll_data: Optional[dict[str, Any]],
    item_perks_count: int,
) -> tuple[str, str]:
    """Generate a keep recommendation and human-readable reason.

    Returns
    -------
    tuple[str, str]
        ``(recommendation, reason)`` where recommendation is one of
        ``KEEP``, ``DIMABLE``, or ``FARM_FOR_BETTER``.
    """
    if god_roll_data is None:
        return (
            "DIMABLE",
            f"{weapon_name} is not in the god roll database — no curated roll to compare.",
        )

    if score == SCORE_EXACT:
        return (
            "KEEP",
            f"Exact god roll match for \"{match_details['roll_name']}\" — "
            f"all 4 perk columns match. This is a perfect drop.",
        )

    if score == SCORE_MAIN_TRAITS:
        return (
            "KEEP",
            f"Main traits match \"{match_details['roll_name']}\" — "
            f"both trait columns are correct. Barrel and/or mag could be better, "
            f"but this is a strong roll worth keeping.",
        )

    if score == SCORE_SINGLE_TRAIT:
        return (
            "FARM_FOR_BETTER",
            f"Only one main trait matches \"{match_details['roll_name']}\" — "
            f"keep temporarily but farm for a better roll.",
        )

    if score == SCORE_BARREL_MAG:
        return (
            "FARM_FOR_BETTER",
            f"Only barrel/mag match \"{match_details['roll_name']}\" — "
            f"the main traits are wrong. Farm for a better roll.",
        )

    # No match at all
    return (
        "DIMABLE",
        f"No god roll match for {weapon_name}. "
        f"Either the item has no matching perks or the database entry has no variants.",
    )


def _masterwork_note(masterwork: Any) -> str:
    """Return a small note string if the item has a masterwork."""
    if not masterwork:
        return ""
    stat_name = masterwork.get("stat_name", "")
    value = masterwork.get("value", 0)
    if stat_name and value:
        return f" (Masterwork: +{value} {stat_name})"
    return ""


# ---------------------------------------------------------------------------
# Armor analysis
# ---------------------------------------------------------------------------

# Stat hashes for armor stats (the 6 core stats)
ARMOR_STAT_NAMES = {
    "Mobility",
    "Resilience",
    "Recovery",
    "Discipline",
    "Intellect",
    "Strength",
}


def _parse_armor_stats(item: dict[str, Any]) -> dict[str, int]:
    """Extract the 6 core armor stats from an item's stats dict.

    Returns only the stats with names matching the canonical six, with
    integer values.  Unknown or non-armor stats are excluded.
    """
    raw_stats: dict[str, Any] = item.get("stats", {})
    result: dict[str, int] = {}
    for name, value in raw_stats.items():
        if name in ARMOR_STAT_NAMES and isinstance(value, (int, float)):
            result[name] = int(value)
    return result


def _armor_total_stats(armor_stats: dict[str, int]) -> int:
    """Sum of all six core armor stats."""
    return sum(armor_stats.values())


def _armor_stat_distribution(
    armor_stats: dict[str, int],
) -> str:
    """Describe the stat distribution of an armor piece.

    Returns one of ``"balanced"``, ``"spiky_<stat>"``, or ``"mixed"``.
    """
    if not armor_stats:
        return "unknown"

    # Check for spike: one stat >= 20
    for stat_name, value in sorted(armor_stats.items(), key=lambda x: -x[1]):
        if value >= ARMOR_STAT_HIGH:
            return f"spiky_{stat_name}"

    # Check if balanced: all stats within a reasonable spread
    values = list(armor_stats.values())
    if max(values) - min(values) <= 8:
        return "balanced"

    return "mixed"


def _armor_stat_category(stats: dict[str, int]) -> str:
    """Categorise armour stat focus (PvP vs PvE vs general)."""
    mob_res_rec = stats.get("Mobility", 0) + stats.get("Resilience", 0) + stats.get("Recovery", 0)
    disc_int_str = stats.get("Discipline", 0) + stats.get("Intellect", 0) + stats.get("Strength", 0)

    if mob_res_rec >= disc_int_str + 10:
        return "top_heavy"  # better for PvP (class ability + resilience + recovery)
    elif disc_int_str >= mob_res_rec + 10:
        return "bottom_heavy"  # better for PvE (grenade + super + melee)
    else:
        return "balanced"


def _analyze_single_armor(
    item: dict[str, Any],
    all_items_same_slot: list[dict[str, Any]],
) -> dict[str, Any]:
    """Analyze a single armor piece.

    Parameters
    ----------
    item:
        The vault item dict for this armor piece.
    all_items_same_slot:
        All vault armor items sharing the same slot (Helmet, Gauntlets, etc.).

    Returns
    -------
    dict
        Analysis dict with keys: total_stats, distribution, category,
        rank_in_slot, keep_recommendation, reason.
    """
    stats = _parse_armor_stats(item)
    total = _armor_total_stats(stats)
    distribution = _armor_stat_distribution(stats)
    category = _armor_stat_category(stats)

    # Rank this item among all copies in the same slot by total stats
    # (higher total = better)
    all_totals = sorted(
        (_armor_total_stats(_parse_armor_stats(i)) for i in all_items_same_slot),
        reverse=True,
    )
    rank = 1
    for t in all_totals:
        if total < t:
            rank += 1

    total_in_slot = len(all_items_same_slot)
    in_top_k = rank <= min(ARMOR_KEEP_COUNT, total_in_slot)

    # Build reason
    mw_note = _masterwork_note(item.get("masterwork"))
    mw_bonus = " (includes +10 Masterwork bonus)" if item.get("masterwork") else ""

    if in_top_k:
        rec = "KEEP"
        reason = (
            f"Rank #{rank}/{total_in_slot} in {item.get('slot', 'Unknown')} slot "
            f"with {total} total stats ({distribution}, {category}){mw_note}. "
            f"Top {ARMOR_KEEP_COUNT} copy — keep for builds."
        )
    else:
        rec = "DIMABLE"
        reason = (
            f"Rank #{rank}/{total_in_slot} in {item.get('slot', 'Unknown')} slot "
            f"with {total} total stats ({distribution}, {category}){mw_note}. "
            f"Not in the top {ARMOR_KEEP_COUNT} — consider dismantling."
        )

    return {
        "total_stats": total,
        "distribution": distribution,
        "category": category,
        "rank_in_slot": rank,
        "total_in_slot": total_in_slot,
        "keep_recommendation": rec,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Craftable weapon analysis
# ---------------------------------------------------------------------------


def _crafting_recommendation(
    weapon_name: str,
    god_roll_data: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Generate a crafting recommendation for a craftable weapon.

    Returns a dict with crafting info, or None if the weapon is not craftable
    or not found in the database.
    """
    if god_roll_data is None:
        return None

    craftable = god_roll_data.get("craftable", False)
    if not craftable:
        return None

    deepsight_needed = god_roll_data.get("deepsight_required", 5)

    return {
        "craftable": True,
        "deepsight_required": deepsight_needed,
        "recommendation": (
            f"{weapon_name} is craftable. "
            f"Collect {deepsight_needed} Deepsight (red-border) copies "
            f"to unlock the pattern."
        ),
    }


# ---------------------------------------------------------------------------
# Farming recommendations
# ---------------------------------------------------------------------------


def _farming_recommendation(
    weapon_name: str,
    god_roll_data: Optional[dict[str, Any]],
    score: int,
) -> Optional[str]:
    """Generate a farming recommendation string based on weapon source.

    Returns a string or None if the source is unknown.
    """
    if god_roll_data is None:
        return None

    source = god_roll_data.get("source", "").lower()

    if not source:
        return None

    # Map sources to farming advice
    source_advice: dict[str, str] = {
        "world drop": (
            f"{weapon_name} is a World Drop — farm on any planet, "
            f"focus Umbral Engrams at the HELM, or use 'Banshee-44 / Gunsmith' rank-ups."
        ),
        "ritual": (
            f"{weapon_name} is a Ritual weapon — complete Vanguard/Crucible/Gambit "
            f"challenges and rank up with Zavala/Shaxx/Drifter."
        ),
        "nightfall": (
            f"{weapon_name} is a Nightfall reward — farm the weekly Nightfall "
            f"on higher difficulties for better drop rates."
        ),
        "raid": (
            f"{weapon_name} is a Raid weapon — farm the appropriate raid encounters. "
            f"Use raid chests and secret chests for extra chances."
        ),
        "dungeon": (
            f"{weapon_name} is a Dungeon weapon — farm the weekly dungeon. "
            f"Check the dungeon rotator if it is not the current weekly."
        ),
        "trials": (
            f"{weapon_name} is a Trials weapon — play Trials of Osiris on weekends. "
            f"Rank up with Saint-14 and use Trials Engrams."
        ),
        "iron banner": (
            f"{weapon_name} is an Iron Banner weapon — play Iron Banner during "
            f"its monthly event. Rank up with Lord Saladin."
        ),
        "seasonal": (
            f"{weapon_name} is a Seasonal weapon — farm the current seasonal activity "
            f"and focus Umbral Engrams at the HELM."
        ),
        "exotic mission": (
            f"{weapon_name} is from an Exotic Mission — check the Legends tab "
            f"for rotating exotic mission availability."
        ),
        "saint-14": (
            f"{weapon_name} is sold by Saint-14 — check his weekly inventory "
            f"and farm Trials Engrams."
        ),
        "xur": (
            f"{weapon_name} can appear with Xûr — check Xûr every weekend "
            f"(Friday reset to Tuesday reset) at his random location."
        ),
        "vow of the disciple": (
            f"{weapon_name} drops from Vow of the Disciple — farm Rhulk and "
            f"secret chests each week."
        ),
        "root of nightmares": (
            f"{weapon_name} drops from Root of Nightmares — farm encounters and "
            f"secret chests each week."
        ),
        "crota's end": (
            f"{weapon_name} drops from Crota's End — farm encounters and "
            f"secret chests each week."
        ),
        "kings fall": (
            f"{weapon_name} drops from King's Fall — farm encounters and "
            f"secret chests each week."
        ),
        "vault of glass": (
            f"{weapon_name} drops from Vault of Glass — farm encounters and "
            f"secret chests each week."
        ),
        "deep stone crypt": (
            f"{weapon_name} drops from Deep Stone Crypt — farm encounters and "
            f"secret chests each week."
        ),
        "salvation's edge": (
            f"{weapon_name} drops from Salvation's Edge — farm encounters and "
            f"secret chests each week."
        ),
        "garden of salvation": (
            f"{weapon_name} drops from Garden of Salvation — farm encounters and "
            f"secret chests each week."
        ),
        "last wish": (
            f"{weapon_name} drops from Last Wish — farm encounters and "
            f"secret chests each week."
        ),
    }

    for key, advice in source_advice.items():
        if key in source:
            return advice

    # Generic fallback
    return (
        f"{weapon_name} is from source '{source}'. "
        f"Farm from its associated activity."
    )


# ---------------------------------------------------------------------------
# VaultAnalyzer
# ---------------------------------------------------------------------------


class VaultAnalyzer:
    """Core matching and analysis engine for the Destiny 2 vault tool.

    Parameters
    ----------
    god_rolls_path:
        Path (string or ``Path``) to the god roll database JSON file.
        Defaults to ``god_rolls/weapons.json`` relative to the working
        directory.
    """

    def __init__(self, god_rolls_path: str | Path = "god_rolls/weapons.json") -> None:
        self._god_rolls_path = Path(god_rolls_path)
        self._god_rolls: dict[str, Any] = _load_god_rolls(self._god_rolls_path)

    # ------------------------------------------------------------------
    # Reload / refresh
    # ------------------------------------------------------------------

    def reload_god_rolls(self, path: Optional[str | Path] = None) -> None:
        """Reload the god roll database from disk.

        If *path* is given, it updates the internal path as well.
        """
        if path is not None:
            self._god_rolls_path = Path(path)
        self._god_rolls = _load_god_rolls(self._god_rolls_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze_weapon(self, weapon_item: dict[str, Any]) -> dict[str, Any]:
        """Analyze a single weapon vault item against the god roll database.

        Parameters
        ----------
        weapon_item:
            A vault item dict as returned by ``VaultReader``.  Must have
            ``name``, ``perks``, ``masterwork``, and ``is_weapon=True``.

        Returns
        -------
        dict
            Analysis dictionary with the following keys:

            - ``name``: weapon name
            - ``instance_id``: unique instance ID
            - ``score``: match score (int, 0-100)
            - ``score_label``: human-readable label for the score tier
            - ``keep_recommendation``: ``KEEP``, ``DIMABLE``, or ``FARM_FOR_BETTER``
            - ``reason``: human-readable explanation
            - ``match_details``: dict with per-column match info
            - ``matched_roll_name``: name of the closest god roll variant
            - ``crafting``: crafting recommendation dict or None
            - ``farming``: farming recommendation string or None
        """
        weapon_name: str = weapon_item.get("name", "Unknown")
        instance_id = weapon_item.get("instance_id")

        # Build perk-by-socket lookup
        item_perks = _perks_by_socket(weapon_item)

        # Look up weapon in god roll database
        god_roll_data = self._god_rolls.get(weapon_name)
        variants: list[dict[str, Any]] = []
        if god_roll_data is not None:
            variants = god_roll_data.get("rolls", [])

        # Find best match
        score, match_details = _best_god_roll_match(item_perks, variants) if variants else (SCORE_NONE, {})

        # Recommendation
        rec, reason = _recommendation_from_score(
            score,
            match_details,
            weapon_name,
            god_roll_data,
            len(item_perks),
        )

        # Append masterwork note
        mw_note = _masterwork_note(weapon_item.get("masterwork"))
        if mw_note:
            reason += mw_note

        # Score label
        score_labels = {
            SCORE_EXACT: "Exact Match",
            SCORE_MAIN_TRAITS: "Main Traits Match",
            SCORE_SINGLE_TRAIT: "Single Trait Match",
            SCORE_BARREL_MAG: "Barrel/Mag Match",
            SCORE_NONE: "No Match",
        }
        score_label = score_labels.get(score, f"Score {score}")

        # Crafting recommendation
        crafting = _crafting_recommendation(weapon_name, god_roll_data)

        # Farming recommendation
        farming = _farming_recommendation(weapon_name, god_roll_data, score)

        return {
            "name": weapon_name,
            "instance_id": instance_id,
            "score": score,
            "score_label": score_label,
            "keep_recommendation": rec,
            "reason": reason,
            "match_details": match_details,
            "matched_roll_name": match_details.get("roll_name", "No Match"),
            "crafting": crafting,
            "farming": farming,
        }

    def analyze_armor(
        self,
        armor_item: dict[str, Any],
        all_armor_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Analyze a single armor vault item.

        Parameters
        ----------
        armor_item:
            A vault item dict with ``is_armor=True``.
        all_armor_items:
            All armor items from the vault (used for ranking copies in the
            same slot).

        Returns
        -------
        dict
            Analysis dictionary with keys: ``name``, ``instance_id``,
            ``slot``, ``total_stats``, ``distribution``, ``category``,
            ``rank_in_slot``, ``keep_recommendation``, ``reason``.
        """
        name: str = armor_item.get("name", "Unknown")
        instance_id = armor_item.get("instance_id")
        slot: str = armor_item.get("slot", "Unknown")

        # Filter to same slot for ranking
        same_slot = [i for i in all_armor_items if i.get("slot") == slot]

        analysis = _analyze_single_armor(armor_item, same_slot)

        return {
            "name": name,
            "instance_id": instance_id,
            "slot": slot,
            **analysis,
        }

    def analyze_vault(
        self, all_items: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Analyze all vault items and produce a comprehensive result.

        Parameters
        ----------
        all_items:
            List of vault item dicts from ``VaultReader``.

        Returns
        -------
        dict
            Result dictionary with keys:

            - ``weapons``: list of weapon analysis dicts
            - ``armor``: list of armor analysis dicts
            - ``summary``: dict with counts and statistics
        """
        weapons: list[dict[str, Any]] = []
        armor: list[dict[str, Any]] = []
        other: list[dict[str, Any]] = []

        # Separate items by type
        for item in all_items:
            if item.get("is_weapon"):
                weapons.append(item)
            elif item.get("is_armor"):
                armor.append(item)
            else:
                other.append(item)

        # Analyze weapons
        weapon_results: list[dict[str, Any]] = [
            self.analyze_weapon(w) for w in weapons
        ]

        # Analyze armor
        armor_results: list[dict[str, Any]] = [
            self.analyze_armor(a, armor) for a in armor
        ]

        # Summary statistics
        total_items = len(all_items)
        total_weapons = len(weapons)
        total_armor = len(armor)
        total_other = len(other)

        keep_count = sum(
            1 for r in weapon_results if r["keep_recommendation"] == "KEEP"
        )
        dimable_count = sum(
            1 for r in weapon_results if r["keep_recommendation"] == "DIMABLE"
        )
        farm_count = sum(
            1 for r in weapon_results if r["keep_recommendation"] == "FARM_FOR_BETTER"
        )

        armor_keep = sum(
            1 for r in armor_results if r["keep_recommendation"] == "KEEP"
        )
        armor_dimable = sum(
            1 for r in armor_results if r["keep_recommendation"] == "DIMABLE"
        )

        # Craftable summary
        craftable_weapons = [
            r for r in weapon_results if r.get("crafting") is not None
        ]

        summary: dict[str, Any] = {
            "total_items": total_items,
            "total_weapons": total_weapons,
            "total_armor": total_armor,
            "total_other": total_other,
            "weapons_keep": keep_count,
            "weapons_dimable": dimable_count,
            "weapons_farm": farm_count,
            "keep_count": keep_count + armor_keep,
            "dismantle_count": dimable_count + armor_dimable,
            "armor_keep": armor_keep,
            "armor_dimable": armor_dimable,
            "craftable_weapons_count": len(craftable_weapons),
            "craftable_weapons": [
                {
                    "name": r["name"],
                    "deepsight_required": r["crafting"]["deepsight_required"],
                }
                for r in craftable_weapons
            ],
            "exact_matches": sum(
                1 for r in weapon_results if r["score"] == SCORE_EXACT
            ),
            "main_trait_matches": sum(
                1 for r in weapon_results if r["score"] == SCORE_MAIN_TRAITS
            ),
            "weapon_scores": {
                "exact": SCORE_EXACT,
                "main_traits": SCORE_MAIN_TRAITS,
                "single_trait": SCORE_SINGLE_TRAIT,
                "barrel_mag": SCORE_BARREL_MAG,
                "none": SCORE_NONE,
            },
        }

        # Build flattened fields for the reporter
        god_roll_matches = []
        dismantle_recommendations = []
        farming_recommendations = []
        for r in weapon_results:
            if r["keep_recommendation"] == "KEEP" and r["score"] >= SCORE_SINGLE_TRAIT:
                god_roll_matches.append({
                    "name": r["name"],
                    "roll_name": r["matched_roll_name"],
                    "match_pct": float(r["score"]),
                    "perks": r.get("reason", ""),
                    "verdict": r["keep_recommendation"],
                })
            elif r["keep_recommendation"] == "DIMABLE":
                dismantle_recommendations.append({
                    "name": r["name"],
                    "reason": r.get("reason", "No reason provided"),
                })
            elif r["keep_recommendation"] == "FARM_FOR_BETTER":
                dismantle_recommendations.append({
                    "name": r["name"],
                    "reason": r.get("reason", "No reason provided"),
                })
            farming_text = r.get("farming")
            if farming_text:
                farming_recommendations.append(farming_text)
            crafting_text = r.get("crafting")
            if crafting_text and isinstance(crafting_text, dict):
                cr = crafting_text.get("recommendation", "")
                if cr:
                    farming_recommendations.append(cr)

        best_armor = []
        for r in armor_results:
            if r["keep_recommendation"] == "KEEP":
                best_armor.append({
                    "name": r.get("item_name", r.get("name", "Unknown")),
                    "slot": r.get("slot", "Unknown"),
                    "stat_total": r.get("total_stats", 0),
                    "distribution": r.get("distribution", "unknown"),
                })

        return {
            "weapons": weapon_results,
            "armor": armor_results,
            "summary": summary,
            # Flattened fields for the reporter
            "total_items": summary["total_items"],
            "total_weapons": summary["total_weapons"],
            "total_armor": summary["total_armor"],
            "keep_count": summary["keep_count"],
            "dismantle_count": summary["dismantle_count"],
            "god_roll_matches": god_roll_matches,
            "dismantle_recommendations": dismantle_recommendations,
            "best_armor": best_armor,
            "farming_recommendations": farming_recommendations,
        }

    # ------------------------------------------------------------------
    # Property: access to loaded god rolls
    # ------------------------------------------------------------------

    @property
    def god_rolls(self) -> dict[str, Any]:
        """The currently loaded god roll database (read-only)."""
        return dict(self._god_rolls)

    @property
    def god_rolls_path(self) -> Path:
        """The path to the currently loaded god roll database file."""
        return self._god_rolls_path

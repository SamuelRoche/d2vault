"""
CLI Entry Point
===============
Main command-line interface for destiny-vault-tool.

Provides:
  - First-run setup wizard (–setup) to configure API key, platform, display name
  - ``analyze`` / ``scan``: load inventory, match against god rolls, produce report
  - ``update-god-rolls``: placeholder for later god-roll fetching
  - ``--html``, ``--quick``, ``--output DIR`` flags

Usage
-----
    python -m src.cli --setup
    python -m src.cli analyze
    python -m src.cli analyze --html
    python -m src.cli analyze --quick
    python -m src.cli analyze --output ./reports
    python -m src.cli scan
    python -m src.cli update-god-rolls
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Project root & config path
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"

# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------

PLATFORM_MAP: dict[str, int] = {
    "steam": 3,
    "xbox": 1,
    "ps": 2,
}

PLATFORM_CHOICES = sorted(PLATFORM_MAP.keys())


def _resolve_membership_type(value: str) -> int:
    """Convert a platform name string to its Bungie membership type int."""
    return PLATFORM_MAP[value.lower().strip()]


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def load_config() -> dict[str, Any] | None:
    """Load config.yaml from the project root.

    Returns the parsed dict, or *None* if the file doesn't exist.
    """
    try:
        import yaml
    except ImportError:
        print("ERROR: PyYAML is required. Install it with: pip install pyyaml")
        sys.exit(1)

    if not CONFIG_PATH.is_file():
        return None

    with CONFIG_PATH.open("r") as fh:
        return yaml.safe_load(fh)


def save_config(cfg: dict[str, Any]) -> None:
    """Write *cfg* to config.yaml in the project root."""
    try:
        import yaml
    except ImportError:
        print("ERROR: PyYAML is required. Install it with: pip install pyyaml")
        sys.exit(1)

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("w") as fh:
        yaml.dump(cfg, fh, default_flow_style=False)
    print(f"Configuration saved to {CONFIG_PATH.resolve()}")


# ---------------------------------------------------------------------------
# Setup wizard
# ---------------------------------------------------------------------------


def _prompt(prompt_text: str, default: str | None = None) -> str:
    """Prompt the user for input with an optional default."""
    if default:
        label = f"{prompt_text} [{default}]: "
    else:
        label = f"{prompt_text}: "
    value = input(label).strip()
    if not value and default is not None:
        return default
    return value


def cmd_setup() -> None:
    """First-run setup wizard — collects credentials and searches for the player.

    Writes config.yaml on success.
    """
    print("=" * 60)
    print("  Destiny Vault Tool — First-Run Setup")
    print("=" * 60)
    print()

    # 1. API key
    api_key = _prompt("Enter your Bungie.net API key")
    if not api_key:
        print("ERROR: API key is required. Visit https://www.bungie.net/en/Application")
        sys.exit(1)

    # 2. Platform
    platform_str = _prompt(
        f"Gaming platform ({'/'.join(PLATFORM_CHOICES)})", default="steam"
    ).lower()
    if platform_str not in PLATFORM_MAP:
        print(
            f"ERROR: Unknown platform '{platform_str}'. "
            f"Choose from: {', '.join(PLATFORM_CHOICES)}"
        )
        sys.exit(1)
    membership_type = PLATFORM_MAP[platform_str]

    # 3. Display name
    display_name = _prompt("Your Bungie display name (e.g. Guardian#1234)")
    if not display_name:
        print("ERROR: Display name is required.")
        sys.exit(1)

    # 4. Auto-search via Bungie API
    print(f"\nSearching for player '{display_name}' on {platform_str}…")
    from src.bungie_api import BungieAPI

    api = BungieAPI(api_key)
    try:
        results = api.search_destiny_player(display_name, membership_type)
    except Exception as exc:
        print(f"ERROR: Failed to search for player: {exc}")
        sys.exit(1)

    if not results:
        print(
            f"ERROR: No player found for '{display_name}' on {platform_str}. "
            f"Check your display name and try again."
        )
        sys.exit(1)

    # Pick the first (best) match
    player = results[0]
    membership_id = player["membershipId"]
    actual_name = player.get("displayName", display_name)

    print(f"  ✓ Found: {actual_name} (membership_id={membership_id})")
    print()

    # 5. Save
    cfg: dict[str, Any] = {
        "api_key": api_key,
        "platform": platform_str,
        "membership_type": membership_type,
        "membership_id": membership_id,
        "display_name": actual_name,
    }
    save_config(cfg)
    print("\nSetup complete! Run 'python -m src.cli analyze' to scan your vault.")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Analyze / Scan
# ---------------------------------------------------------------------------


def _import_matcher() -> Any | None:
    """Try to import the matcher module; return the VaultAnalyzer or None."""
    try:
        from src.matcher import VaultAnalyzer  # noqa: F401

        return VaultAnalyzer
    except ImportError:
        return None


def _import_reporter() -> tuple[Any, Any] | None:
    """Try to import the reporter module; return (print_terminal_report,
    generate_html_report) or None."""
    try:
        from src.reporter import generate_html_report, print_terminal_report  # noqa: F401

        return print_terminal_report, generate_html_report
    except ImportError:
        return None


def cmd_analyze(args: argparse.Namespace) -> None:
    """Load config, download manifest, read vault, match god rolls, report."""
    # ---- Load config ----
    cfg = load_config()
    if cfg is None:
        print(
            "ERROR: No configuration found. Run with --setup first:\n"
            f"    python -m src.cli --setup"
        )
        sys.exit(1)

    api_key: str = cfg.get("api_key", "")
    membership_type: int = cfg.get("membership_type", 3)
    membership_id: int = cfg.get("membership_id", 0)
    display_name: str = cfg.get("display_name", "Guardian")
    platform_str: str = cfg.get("platform", "steam")

    if not api_key:
        print("ERROR: config.yaml is missing 'api_key'. Re-run with --setup.")
        sys.exit(1)
    if not membership_id:
        print("ERROR: config.yaml is missing 'membership_id'. Re-run with --setup.")
        sys.exit(1)

    # ---- Connect to API ----
    print(f"Connecting to Bungie API as {display_name} ({platform_str})…")
    from src.bungie_api import BungieAPI

    api = BungieAPI(api_key)

    # ---- Manifest ----
    from src.manifest_cache import ManifestCache

    mc = ManifestCache(api_key)

    quick = getattr(args, "quick", False)

    if quick:
        # Use cached manifest; just check existence
        from src.manifest_cache import DB_PATH as MANIFEST_DB_PATH

        if not MANIFEST_DB_PATH.exists():
            print(
                "ERROR: --quick was specified but no cached manifest found. "
                "Run without --quick first to download the manifest."
            )
            sys.exit(1)
        print("Using cached manifest (--quick).")
    else:
        print("Checking manifest version…")
        if mc.needs_update():
            print("Downloading latest manifest…")
            mc.download_manifest(
                progress_callback=lambda pct, msg: print(
                    f"  [{pct * 100:3.0f}%] {msg}"
                )
            )
        else:
            print("Manifest is up to date.")

    # ---- Read inventories ----
    print("\nReading vault and character inventories…")
    from src.vault_reader import collect_all_inventories

    try:
        items = collect_all_inventories(api, membership_type, membership_id, mc)
    except Exception as exc:
        print(f"ERROR: Failed to read inventories: {exc}")
        sys.exit(1)

    if not items:
        print("No legendary+ items found in vault or on characters.")
        sys.exit(0)

    print(f"  Found {len(items)} legendary+ items.")

    # ---- Match against god rolls (if matcher available) ----
    VaultAnalyzer = _import_matcher()
    if VaultAnalyzer is not None:
        print("\nAnalyzing god rolls…")
        analyzer = VaultAnalyzer(mc)
        matched_items = analyzer.match_all(items)
        summary = analyzer.summarize(matched_items)
    else:
        print("  (matcher module not installed — skipping god-roll analysis)")
        matched_items: list[dict[str, Any]] = []  # type: ignore[no-redef]
        summary: dict[str, Any] = {  # type: ignore[no-redef]
            "total": len(items),
            "keeps": len(items),
            "dismantle": 0,
            "god_rolls": 0,
        }

    # ---- Report ----
    reporters = _import_reporter()
    if reporters is not None:
        print_terminal_report, generate_html_report = reporters
    else:
        print_terminal_report = None
        generate_html_report = None

    # Terminal report
    if print_terminal_report is not None:
        print_terminal_report(items, matched_items, summary)
    else:
        _print_fallback_report(items, summary)

    # HTML report (optional)
    html = getattr(args, "html", False)
    output_dir = getattr(args, "output", None)

    if html or output_dir:
        if generate_html_report is not None:
            if output_dir:
                out = Path(output_dir)
            else:
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                out = PROJECT_ROOT / "reports"
                out.mkdir(parents=True, exist_ok=True)
                out = out / f"vault_report_{timestamp}.html"

            print(f"\nGenerating HTML report → {out.resolve()}")
            try:
                generate_html_report(
                    items,
                    matched_items,
                    summary,
                    output_path=str(out),
                    player_name=display_name,
                )
            except Exception as exc:
                print(f"ERROR: Failed to write HTML report: {exc}")
        else:
            print(
                "\nNOTE: reporter module not installed. "
                "Install it or create src/reporter.py to enable HTML reports."
            )

    mc.close()


def _print_fallback_report(
    items: list[dict[str, Any]], summary: dict[str, Any]
) -> None:
    """Simple terminal report when the reporter module is unavailable."""
    print()
    print("=" * 60)
    print("  Vault Analysis Summary")
    print("=" * 60)
    total = summary.get("total", len(items))
    keeps = summary.get("keeps", len(items))
    dismantle = summary.get("dismantle", 0)
    god_rolls = summary.get("god_rolls", 0)
    print(f"  Total items:          {total}")
    print(f"  Keep-worthy:          {keeps}")
    print(f"  Dismantle candidates: {dismantle}")
    print(f"  God rolls:            {god_rolls}")
    print()

    # Show item list by slot
    slots: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        slot = item.get("slot", "Unknown")
        slots.setdefault(slot, []).append(item)

    for slot_name in sorted(slots.keys()):
        slot_items = slots[slot_name]
        print(f"  [{slot_name}] ({len(slot_items)} items)")
        for item in slot_items:
            name = item.get("name", "Unknown")
            tier = item.get("tier", 0)
            location = item.get("location", "")
            tier_label = {5: "Legendary", 6: "Exotic", 7: "Exotic"}.get(tier, str(tier))
            loc_str = f" [{location}]" if location else ""
            print(f"    {tier_label:12s} {name}{loc_str}")
        print()
    print("=" * 60)


# ---------------------------------------------------------------------------
# Update god rolls (placeholder)
# ---------------------------------------------------------------------------


def cmd_update_god_rolls() -> None:
    """Fetch latest god rolls (future)."""
    print(
        "The 'update-god-rolls' command is not yet implemented.\n"
        "When available, it will fetch the latest curated god-roll "
        "definitions from a remote source and store them locally."
    )


# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser."""
    parser = argparse.ArgumentParser(
        prog="destiny-vault-tool",
        description="Analyze your Destiny 2 vault items against curated god rolls.",
        epilog=(
            "Examples:\n"
            "  %(prog)s --setup                   First-run configuration wizard\n"
            "  %(prog)s analyze                   Scan vault and print report\n"
            "  %(prog)s analyze --html --quick    Use cached manifest + HTML report\n"
            "  %(prog)s analyze --output ./reports\n"
            "  %(prog)s scan                      Alias for 'analyze'\n"
            "  %(prog)s update-god-rolls          (future) fetch latest god rolls\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # -- Global flags -------------------------------------------------------
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Run the first-time setup wizard (API key, platform, display name).",
    )

    # -- Subcommands ---------------------------------------------------------
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # analyze
    analyze_parser = subparsers.add_parser(
        "analyze",
        aliases=[],
        help="Scan vault and character inventories, match god rolls, print report.",
        description=(
            "Analyze your Destiny 2 vault and character inventories.\n\n"
            "Loads the configuration from config.yaml, connects to the Bungie API,\n"
            "downloads (or reuses) the manifest database, reads all legendary+ items\n"
            "from your vault and characters, matches them against curated god rolls,\n"
            "and outputs a terminal report. Optionally generates an HTML report."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    analyze_parser.add_argument(
        "--html",
        action="store_true",
        help="Also generate an HTML report file.",
    )
    analyze_parser.add_argument(
        "--quick",
        action="store_true",
        help="Skip manifest download; use the locally cached version. "
        "Fails if no cached manifest exists.",
    )
    analyze_parser.add_argument(
        "--output",
        type=str,
        default=None,
        metavar="DIR",
        help="Output directory for the HTML report. "
        "Defaults to reports/vault_report_<timestamp>.html.",
    )

    # scan (alias for analyze)
    scan_parser = subparsers.add_parser(
        "scan",
        help="Alias for 'analyze'. Scan vault and print report.",
        description=(
            "Alias for the 'analyze' command. See 'analyze --help' for details."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    scan_parser.add_argument(
        "--html",
        action="store_true",
        help="Also generate an HTML report file.",
    )
    scan_parser.add_argument(
        "--quick",
        action="store_true",
        help="Skip manifest download; use cached version.",
    )
    scan_parser.add_argument(
        "--output",
        type=str,
        default=None,
        metavar="DIR",
        help="Output directory for the HTML report.",
    )

    # update-god-rolls
    subparsers.add_parser(
        "update-god-rolls",
        help="(future) Fetch the latest curated god roll definitions.",
        description=(
            "Fetch the latest curated god-roll definitions from a remote source\n"
            "and store them locally.  Not yet implemented."
        ),
    )

    return parser


def main(argv: list[str] | None = None) -> None:
    """CLI entry point. Parses arguments and dispatches to the appropriate
    command handler."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # --setup is a global flag, not a subcommand
    if args.setup:
        cmd_setup()
        return

    # Dispatch subcommand
    if args.command == "analyze" or args.command == "scan":
        cmd_analyze(args)
    elif args.command == "update-god-rolls":
        cmd_update_god_rolls()
    else:
        # No subcommand given and no --setup: default to analyze
        # Re-parse with analyze as default (argparse doesn't have a clean
        # "default subcommand" mechanism, so we emulate it).
        default_args = ["analyze"] + (sys.argv[1:] if argv is None else argv[1:])
        # But filter out any flags that might confuse analyze (like --setup is
        # already handled above).  Also avoid double-dispatch loops.
        if not any(a in default_args for a in ("analyze", "scan", "update-god-rolls")):
            # Only insert if no subcommand was already present
            args = parser.parse_args(default_args)
            cmd_analyze(args)
        else:
            parser.print_help()
            sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()

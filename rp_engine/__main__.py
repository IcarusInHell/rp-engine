"""Allow running with `python -m rp_engine` or the `rp-engine` console script.

Default (no subcommand) starts the API server. Subcommands:
  migrate-cards  — convert story cards between legacy YAML and sidecar formats.
"""

import argparse
from pathlib import Path

import uvicorn

from rp_engine.config import get_config


def _run_server(args: argparse.Namespace) -> None:
    uvicorn.run(
        "rp_engine.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


def _run_migrate_cards(args: argparse.Namespace) -> None:
    from rp_engine.migrate_cards import migrate_cards

    vault_root = Path(get_config().paths.vault_root)
    report = migrate_cards(
        vault_root,
        args.rp_folder,
        dry_run=args.dry_run,
        reverse=args.reverse,
    )

    verb = "would change" if args.dry_run else "changed"
    direction = "sidecar → legacy" if args.reverse else "legacy → sidecar"
    print(f"migrate-cards ({direction}, {verb}): {report.summary()}")
    for path in report.migrated:
        print(f"  migrated: {path}")
    for path in report.skipped:
        print(f"  skipped:  {path}")
    for path in report.errors:
        print(f"  ERROR:    {path}")


def main():
    config = get_config()

    parser = argparse.ArgumentParser(description="RP Engine")
    subparsers = parser.add_subparsers(dest="command")

    # Default command: run the server. Flags also live on the top-level parser so
    # `rp-engine --port 9000` (no subcommand) keeps working.
    for p in (parser,):
        p.add_argument("--host", default=config.server.host, help="Bind address (default: %(default)s)")
        p.add_argument("--port", "-p", type=int, default=config.server.port, help="Port (default: %(default)s)")
        p.add_argument("--reload", action="store_true", help="Enable auto-reload for development")

    migrate = subparsers.add_parser(
        "migrate-cards", help="Convert story cards between legacy YAML and sidecar formats"
    )
    migrate.add_argument("rp_folder", nargs="?", default=None, help="RP folder (default: all folders)")
    migrate.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    migrate.add_argument("--reverse", action="store_true", help="Convert sidecar → legacy YAML")
    migrate.set_defaults(func=_run_migrate_cards)

    args = parser.parse_args()

    if getattr(args, "func", None):
        args.func(args)
    else:
        _run_server(args)


if __name__ == "__main__":
    main()

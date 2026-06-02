"""Filesystem monitor for story card changes.

Uses watchfiles to watch Story Cards/**/*.md and RP State/Story_Guidelines.md.
Debounces 500ms to handle Obsidian's atomic save (write temp + rename).
Supervised loop auto-restarts on crash with exponential backoff (1s → 60s).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from rp_engine.services.card_indexer import CardIndexer

logger = logging.getLogger(__name__)

_BACKOFF_BASE = 1.0      # seconds
_BACKOFF_MAX = 60.0       # seconds
_BACKOFF_MULTIPLIER = 2.0


class FileWatcher:
    """Watches story card files and triggers reindexing on changes."""

    def __init__(
        self,
        card_indexer: CardIndexer,
        vault_root: Path,
        rp_folders: list[str],
        debounce_ms: int = 500,
    ) -> None:
        self.card_indexer = card_indexer
        self.vault_root = vault_root
        self.rp_folders = rp_folders
        self.debounce_ms = debounce_ms
        self._task: asyncio.Task | None = None
        self._running = False
        self.diagnostic_logger = None  # injected by container
        self._restart_count = 0

    def start(self) -> None:
        """Start the file watcher as a supervised background task."""
        self._running = True
        self._task = asyncio.create_task(self._supervised_watch_loop())
        logger.info("File watcher started for %d RP folders", len(self.rp_folders))

    async def stop(self) -> None:
        """Stop the file watcher."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("File watcher stopped (restarts: %d)", self._restart_count)

    async def _supervised_watch_loop(self) -> None:
        """Supervise the watch loop with exponential backoff on crash."""
        backoff = _BACKOFF_BASE
        while self._running:
            try:
                await self._watch_loop()
                # _watch_loop returned normally (e.g. no dirs to watch) — don't restart
                break
            except asyncio.CancelledError:
                raise  # propagate cancellation
            except Exception:
                self._restart_count += 1
                logger.exception(
                    "File watcher crashed (restart #%d, backoff %.1fs)",
                    self._restart_count, backoff,
                )
                if self.diagnostic_logger:
                    self.diagnostic_logger.log(
                        category="file_watcher",
                        event="crash_restart",
                        data={
                            "restart_count": self._restart_count,
                            "backoff_seconds": backoff,
                        },
                    )
                if not self._running:
                    break
                await asyncio.sleep(backoff)
                backoff = min(backoff * _BACKOFF_MULTIPLIER, _BACKOFF_MAX)

    def _get_watch_paths(self) -> list[Path]:
        """Get all directories to watch."""
        paths = []
        for folder in self.rp_folders:
            story_cards = self.vault_root / folder / "Story Cards"
            if story_cards.is_dir():
                paths.append(story_cards)
            rp_state = self.vault_root / folder / "RP State"
            if rp_state.is_dir():
                paths.append(rp_state)
        return paths

    def _find_rp_folder(self, file_path: Path) -> str | None:
        """Determine which RP folder a file belongs to."""
        try:
            rel = file_path.relative_to(self.vault_root)
        except ValueError:
            return None
        parts = rel.parts
        if parts and parts[0] in self.rp_folders:
            return parts[0]
        return None

    async def _handle_sidecar_change(
        self, rp_folder: str, sidecar_path: Path, change_type, watchfiles
    ) -> None:
        """Re-index the .md paired with a changed ``.meta/{stem}.json`` sidecar.

        ``.meta/Foo.json`` pairs with ``../Foo.md``. Sidecar add/modify re-indexes
        the card. Sidecar deletion is logged explicitly (not swallowed): the body-
        only ``.md`` then has no metadata, so it can no longer parse as a card —
        we re-index so a card that still carries inline YAML is picked up, and warn
        loudly if a now-orphaned body-only ``.md`` is left behind.
        """
        md_path = sidecar_path.parent.parent / f"{sidecar_path.stem}.md"
        if not md_path.exists():
            logger.debug("Sidecar change with no paired .md: %s", sidecar_path)
            return

        if change_type == watchfiles.Change.deleted:
            reindexed = await self.card_indexer.index_file(rp_folder, md_path)
            if not reindexed:
                logger.warning(
                    "Sidecar removed (%s); paired .md %s no longer parses as a "
                    "card — index entry may be stale until the .md is edited or "
                    "removed",
                    sidecar_path, md_path,
                )
            return

        await self.card_indexer.index_file(rp_folder, md_path)
        logger.debug("Reindexed via sidecar change: %s → %s", sidecar_path, md_path)

    async def _watch_loop(self) -> None:
        """Main watch loop using watchfiles."""
        import watchfiles

        watch_paths = self._get_watch_paths()
        if not watch_paths:
            logger.warning("No directories to watch")
            return

        logger.info("Watching paths: %s", [str(p) for p in watch_paths])

        try:
            async for changes in watchfiles.awatch(
                *watch_paths,
                debounce=self.debounce_ms,
                step=100,
                stop_event=asyncio.Event() if not self._running else None,
            ):
                if not self._running:
                    break

                for change_type, path_str in changes:
                    file_path = Path(path_str)

                    is_sidecar = (
                        file_path.suffix == ".json"
                        and file_path.parent.name == ".meta"
                    )
                    # Only .md cards and their .meta/*.json sidecars are relevant.
                    if file_path.suffix != ".md" and not is_sidecar:
                        continue

                    rp_folder = self._find_rp_folder(file_path)
                    if not rp_folder:
                        continue

                    try:
                        if is_sidecar:
                            await self._handle_sidecar_change(
                                rp_folder, file_path, change_type, watchfiles
                            )
                        elif change_type in (
                            watchfiles.Change.added,
                            watchfiles.Change.modified,
                        ):
                            await self.card_indexer.index_file(rp_folder, file_path)
                            logger.debug("Reindexed: %s", file_path)
                        elif change_type == watchfiles.Change.deleted:
                            await self.card_indexer.remove_file(rp_folder, file_path)
                            logger.debug("Removed: %s", file_path)
                    except Exception:
                        logger.exception("Error processing file change: %s", file_path)

        except asyncio.CancelledError:
            logger.debug("File watcher cancelled")
        except Exception:
            logger.exception("File watcher error")

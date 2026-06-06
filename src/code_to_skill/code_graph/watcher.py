"""文件监听增量同步 — 对齐 external/codegraph 运行时增量更新。"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)


class IncrementalGraphWatcher:
    """监听仓库变更，防抖后触发增量图谱构建。"""

    def __init__(
        self,
        repo_root: str,
        output_root: str,
        *,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        debounce_sec: float = 2.0,
        on_sync: Callable[[dict], None] | None = None,
    ):
        self.repo_root = os.path.abspath(repo_root)
        self.output_root = output_root
        self.include = include
        self.exclude = exclude
        self.debounce_sec = debounce_sec
        self.on_sync = on_sync
        self._observer = None
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def start(self) -> "IncrementalGraphWatcher":
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError as exc:
            raise RuntimeError(
                "watchdog not installed; run: pip install 'code-to-skill[codegraph]'"
            ) from exc

        watcher = self

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                if event.is_directory:
                    return
                src = getattr(event, "src_path", "")
                if not _is_source_file(src):
                    return
                watcher._schedule_sync()

        self._observer = Observer()
        self._observer.schedule(_Handler(), self.repo_root, recursive=True)
        self._observer.start()
        logger.info("[GraphWatcher] watching %s", self.repo_root)
        return self

    def stop(self):
        if self._timer:
            self._timer.cancel()
            self._timer = None
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        logger.info("[GraphWatcher] stopped")

    def _schedule_sync(self):
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce_sec, self._run_sync)
            self._timer.daemon = True
            self._timer.start()

    def _run_sync(self):
        from . import run_code_graph_pipeline

        try:
            result = run_code_graph_pipeline(
                repo_root=self.repo_root,
                include=self.include,
                exclude=self.exclude,
                output_root=self.output_root,
                use_cache=True,
            )
            graph = result.get("graph")
            node_count = len(graph.nodes) if graph else 0
            logger.info("[GraphWatcher] synced: %d nodes", node_count)
            if self.on_sync:
                self.on_sync(result)
        except Exception:
            logger.exception("[GraphWatcher] sync failed")


def watch_repo(
    repo_root: str,
    output_root: str,
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    debounce_sec: float = 2.0,
    run_forever: bool = True,
) -> IncrementalGraphWatcher:
    """启动监听；run_forever=True 时阻塞直到 KeyboardInterrupt。"""
    w = IncrementalGraphWatcher(
        repo_root, output_root,
        include=include, exclude=exclude, debounce_sec=debounce_sec,
    ).start()
    if run_forever:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            w.stop()
    return w


def _is_source_file(path: str) -> bool:
    if any(x in path for x in ("/.git/", "/node_modules/", "/target/", "/build/", "/.codegraph/")):
        return False
    return path.endswith((
        ".java", ".py", ".js", ".ts", ".go", ".rs", ".kt",
        ".xml", ".yaml", ".yml",
    ))

"""CodeGraph MCP Daemon — 文件监听 + MCP stdio 一体运行。"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="CodeGraph MCP daemon (watch + stdio)")
    parser.add_argument("--db", dest="db_path", required=True, help="graph.db path")
    parser.add_argument("--repo-root", dest="repo_root", required=True)
    parser.add_argument("--output", dest="output_root", default=None, help="增量写入目录")
    parser.add_argument("--debounce", type=float, default=2.0)
    parser.add_argument("--no-watch", action="store_true", help="仅启动 MCP，不监听文件")
    args = parser.parse_args(argv)

    os.environ["CODEGRAPH_DB_PATH"] = os.path.abspath(args.db_path)
    os.environ["CODEGRAPH_REPO_ROOT"] = os.path.abspath(args.repo_root)

    output = args.output_root or os.path.dirname(os.path.abspath(args.db_path))

    if not args.no_watch:
        from code_to_skill.code_graph.watcher import IncrementalGraphWatcher

        def _on_sync(result):
            from code_to_skill.codegraph_mcp.registry_holder import invalidate_registry

            invalidate_registry()
            n = len(result.get("graph").nodes) if result.get("graph") else 0
            logger.info("[daemon] graph refreshed: %d nodes (registry cache cleared)", n)

        watcher = IncrementalGraphWatcher(
            args.repo_root, output, debounce_sec=args.debounce, on_sync=_on_sync,
        )
        t = threading.Thread(target=lambda: watcher.start(), daemon=True)
        t.start()
        logger.info("[daemon] file watcher started on %s", args.repo_root)

    from code_to_skill.codegraph_mcp import _create_mcp
    _create_mcp().run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    main()

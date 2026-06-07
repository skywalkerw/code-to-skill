"""Run 级文件日志：将 INFO 及以上日志追加写入 <run_dir>/logs/run.log。"""
from __future__ import annotations

import logging
import os

from .time_utils import LocalTimeFormatter

_RUN_FILE_HANDLER: logging.FileHandler | None = None
_RUN_LOG_PATH: str = ""


def configure_run_logging(run_dir: str, *, log_name: str = "run.log") -> str:
    """为当前 run 目录挂载文件日志 handler（幂等，同路径不重复添加）。"""
    global _RUN_FILE_HANDLER, _RUN_LOG_PATH

    run_dir = str(run_dir).rstrip(os.sep)
    if not run_dir:
        return ""

    log_dir = os.path.join(run_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, log_name)

    if _RUN_LOG_PATH == log_path and _RUN_FILE_HANDLER is not None:
        return log_path

    if _RUN_FILE_HANDLER is not None:
        root = logging.getLogger()
        root.removeHandler(_RUN_FILE_HANDLER)
        _RUN_FILE_HANDLER.close()
        _RUN_FILE_HANDLER = None
        _RUN_LOG_PATH = ""

    handler = logging.FileHandler(log_path, encoding="utf-8", mode="a")
    handler.setFormatter(LocalTimeFormatter(
        fmt="%(asctime)s  %(levelname)-5s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    handler.setLevel(logging.INFO)

    root = logging.getLogger()
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)

    _RUN_FILE_HANDLER = handler
    _RUN_LOG_PATH = log_path
    logging.getLogger(__name__).info("Run log file: %s", log_path)
    return log_path


def get_run_log_path() -> str:
    """返回当前 run 日志文件路径（未配置时为空）。"""
    return _RUN_LOG_PATH

"""系统本地时区时间工具。

所有面向用户/产物的可读时间戳均使用当前系统时区（datetime.now().astimezone()），
不写死特定 zone。
"""
from __future__ import annotations

import logging
from datetime import datetime


def local_now() -> datetime:
    """当前系统本地时区的 aware datetime。"""
    return datetime.now().astimezone()


def local_timestamp() -> str:
    """ISO 8601 本地时间戳，含时区偏移（如 2026-06-06T13:51:09+08:00）。"""
    return local_now().isoformat(timespec="seconds")


def local_timestamp_compact() -> str:
    """紧凑时间戳，用于 run_id 等（YYYYMMDD-HHMMSS）。"""
    return local_now().strftime("%Y%m%d-%H%M%S")


class LocalTimeFormatter(logging.Formatter):
    """logging Formatter：asctime 使用系统本地时区。"""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created).astimezone()
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat(timespec="seconds")

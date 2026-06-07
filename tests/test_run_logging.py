"""Run 文件日志测试。"""
import logging

from code_to_skill.run_logging import configure_run_logging, get_run_log_path


def test_configure_run_logging_writes_file(tmp_path):
    run_dir = tmp_path / "runs" / "test-run"
    log_path = configure_run_logging(str(run_dir))
    assert log_path == str(run_dir / "logs" / "run.log")
    assert get_run_log_path() == log_path

    logging.getLogger("test_run_logging").info("hello run log")
    for handler in logging.getLogger().handlers:
        if hasattr(handler, "flush"):
            handler.flush()

    content = (run_dir / "logs" / "run.log").read_text(encoding="utf-8")
    assert "hello run log" in content
    assert "test_run_logging" in content

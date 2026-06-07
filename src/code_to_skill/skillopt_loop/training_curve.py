"""SkillOpt 训练曲线记录：逐步落盘，便于事后分析与绘图。"""
from __future__ import annotations

import csv
import json
import logging
import os
import re
from typing import Any

from code_to_skill.time_utils import local_timestamp

logger = logging.getLogger(__name__)

CURVE_JSONL = "training_curve.jsonl"
CURVE_JSON = "training_curve.json"
CURVE_CSV = "training_curve.csv"
CURVE_SVG = "training_curve.svg"


class TrainingCurveRecorder:
    """追加式训练曲线记录器。

    - 运行中写入 ``training_curve.jsonl``（崩溃可恢复）
    - 训练结束生成 ``training_curve.json`` + ``training_curve.csv``
    """

    def __init__(self, output_dir: str, *, gate_metric: str = "soft", resume: bool = False):
        self.output_dir = output_dir
        self.gate_metric = gate_metric
        self._points: list[dict[str, Any]] = []
        os.makedirs(output_dir, exist_ok=True)
        if resume:
            self._load_jsonl()

    def _jsonl_path(self) -> str:
        return os.path.join(self.output_dir, CURVE_JSONL)

    def _load_jsonl(self) -> None:
        path = self._jsonl_path()
        if not os.path.isfile(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self._points.append(json.loads(line))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[curve] failed to load %s: %s", path, exc)

    def append(self, event: str, **fields: Any) -> None:
        point = {
            "ts": local_timestamp(),
            "event": event,
            **{k: v for k, v in fields.items() if v is not None},
        }
        self._points.append(point)
        with open(self._jsonl_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(point, ensure_ascii=False) + "\n")

    def record_init(
        self,
        *,
        selection_hard: float,
        selection_soft: float,
        selection_gate: float,
    ) -> None:
        self.append(
            "init",
            step=0,
            epoch=0,
            selection_hard=round(selection_hard, 4),
            selection_soft=round(selection_soft, 4),
            selection_gate=round(selection_gate, 4),
            best_score=round(selection_gate, 4),
            current_score=round(selection_gate, 4),
        )

    def record_rollout(
        self,
        *,
        step: int,
        epoch: int,
        rollout_results: list[dict],
    ) -> None:
        n = max(len(rollout_results), 1)
        passed = sum(1 for r in rollout_results if r.get("hard", 0) == 1)
        failed = len(rollout_results) - passed
        soft = sum(r.get("soft", 0.0) for r in rollout_results) / n
        hard = passed / n
        self.append(
            "rollout",
            step=step,
            epoch=epoch,
            train_rollout_soft=round(soft, 4),
            train_rollout_hard=round(hard, 4),
            train_passed=passed,
            train_failed=failed,
            train_total=len(rollout_results),
        )

    def record_skip(
        self,
        *,
        step: int,
        epoch: int,
        reason: str,
        rollout_results: list[dict] | None = None,
        patch_count: int = 0,
    ) -> None:
        fields: dict[str, Any] = {
            "step": step,
            "epoch": epoch,
            "reason": reason,
            "patch_count": patch_count,
        }
        if rollout_results:
            n = max(len(rollout_results), 1)
            passed = sum(1 for r in rollout_results if r.get("hard", 0) == 1)
            fields.update({
                "train_rollout_soft": round(
                    sum(r.get("soft", 0.0) for r in rollout_results) / n, 4,
                ),
                "train_rollout_hard": round(passed / n, 4),
                "train_passed": passed,
                "train_failed": len(rollout_results) - passed,
                "train_total": len(rollout_results),
            })
        self.append("skip", **fields)

    def record_gate(
        self,
        *,
        step: int,
        epoch: int,
        rollout_results: list[dict],
        selection_hard: float,
        selection_soft: float,
        selection_gate: float,
        best_score: float,
        current_score: float,
        gate_action: str,
        gate_reason: str,
        edit_count: int,
        patch_count: int,
    ) -> None:
        n = max(len(rollout_results), 1)
        passed = sum(1 for r in rollout_results if r.get("hard", 0) == 1)
        self.append(
            "gate",
            step=step,
            epoch=epoch,
            train_rollout_soft=round(
                sum(r.get("soft", 0.0) for r in rollout_results) / n, 4,
            ),
            train_rollout_hard=round(passed / n, 4),
            train_passed=passed,
            train_failed=len(rollout_results) - passed,
            train_total=len(rollout_results),
            selection_hard=round(selection_hard, 4),
            selection_soft=round(selection_soft, 4),
            selection_gate=round(selection_gate, 4),
            best_score=round(best_score, 4),
            current_score=round(current_score, 4),
            gate_action=gate_action,
            gate_reason=gate_reason,
            edit_count=edit_count,
            patch_count=patch_count,
        )

    def record_epoch_end(
        self,
        *,
        step: int,
        epoch: int,
        best_score: float,
        current_score: float,
        slow_update_gate: str | None = None,
        slow_update_reason: str | None = None,
        comparison_pairs: dict | None = None,
    ) -> None:
        fields: dict[str, Any] = {
            "step": step,
            "epoch": epoch,
            "best_score": round(best_score, 4),
            "current_score": round(current_score, 4),
        }
        if slow_update_gate:
            fields["slow_update_gate"] = slow_update_gate
        if slow_update_reason:
            fields["slow_update_reason"] = slow_update_reason
        if comparison_pairs:
            fields["comparison_pairs"] = comparison_pairs
        self.append("epoch_end", **fields)

    def record_test(
        self,
        *,
        test_score: float,
        test_hard: float,
        n_items: int,
        best_score: float,
    ) -> None:
        self.append(
            "test",
            test_score=round(test_score, 4),
            test_hard=round(test_hard, 4),
            test_n_items=n_items,
            best_score=round(best_score, 4),
        )

    def finalize(self, *, best_step: int = 0, test_report: dict | None = None) -> dict:
        """汇总曲线并写入 JSON / CSV。"""
        summary = _build_summary(self._points, best_step=best_step, test_report=test_report)
        payload = {
            "schema_version": "1.0",
            "gate_metric": self.gate_metric,
            "points": self._points,
            "series": _build_series(self._points),
            "summary": summary,
        }
        json_path = os.path.join(self.output_dir, CURVE_JSON)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        _write_csv(self._points, os.path.join(self.output_dir, CURVE_CSV))
        logger.info(
            "[curve] saved %d points → %s, %s",
            len(self._points), CURVE_JSON, CURVE_CSV,
        )
        return payload


def _build_series(points: list[dict]) -> dict[str, list[dict]]:
    """按事件类型抽取常用绘图序列。"""
    series: dict[str, list[dict]] = {
        "train_rollout_soft": [],
        "train_rollout_hard": [],
        "selection_gate": [],
        "selection_hard": [],
        "selection_soft": [],
        "best_score": [],
    }
    best_so_far = 0.0
    for p in points:
        step = p.get("step", 0)
        epoch = p.get("epoch", 0)
        base = {"step": step, "epoch": epoch, "ts": p.get("ts", "")}

        if p.get("event") == "init":
            g = p.get("selection_gate", 0.0)
            best_so_far = max(best_so_far, g)
            series["selection_gate"].append({**base, "value": g})
            series["selection_hard"].append({**base, "value": p.get("selection_hard", 0.0)})
            series["selection_soft"].append({**base, "value": p.get("selection_soft", 0.0)})
            series["best_score"].append({**base, "value": best_so_far})
            continue

        if p.get("train_rollout_soft") is not None:
            series["train_rollout_soft"].append({
                **base, "value": p["train_rollout_soft"],
            })
        if p.get("train_rollout_hard") is not None:
            series["train_rollout_hard"].append({
                **base, "value": p["train_rollout_hard"],
            })

        if p.get("selection_gate") is not None:
            series["selection_gate"].append({**base, "value": p["selection_gate"]})
        if p.get("selection_hard") is not None:
            series["selection_hard"].append({**base, "value": p["selection_hard"]})
        if p.get("selection_soft") is not None:
            series["selection_soft"].append({**base, "value": p["selection_soft"]})

        if p.get("best_score") is not None:
            best_so_far = max(best_so_far, float(p["best_score"]))
            series["best_score"].append({**base, "value": best_so_far})

    if points and points[-1].get("event") == "test":
        tp = points[-1]
        series["test_hard"] = [{"step": tp.get("step", 0), "value": tp.get("test_hard", 0.0)}]
        series["test_soft"] = [{"step": tp.get("step", 0), "value": tp.get("test_score", 0.0)}]

    return series


def _build_summary(
    points: list[dict],
    *,
    best_step: int,
    test_report: dict | None,
) -> dict:
    gate_events = [p for p in points if p.get("event") == "gate"]
    accepts = sum(1 for p in gate_events if p.get("gate_action") in ("accept", "accept_new_best"))
    rejects = sum(1 for p in gate_events if p.get("gate_action") == "reject")
    best_scores = [p.get("best_score", 0.0) for p in points if p.get("best_score") is not None]
    summary: dict[str, Any] = {
        "total_points": len(points),
        "rollout_steps": len([p for p in points if p.get("event") == "rollout"]),
        "gate_steps": len(gate_events),
        "skip_steps": len([p for p in points if p.get("event") == "skip"]),
        "gate_accepts": accepts,
        "gate_rejects": rejects,
        "best_score": max(best_scores) if best_scores else 0.0,
        "best_step": best_step,
    }
    if test_report:
        summary["test_score"] = test_report.get("test_score", 0.0)
        summary["test_hard"] = test_report.get("test_hard", 0.0)
        summary["test_n_items"] = test_report.get("n_items", 0)
    return summary


_CSV_COLUMNS = [
    "ts", "event", "step", "epoch",
    "train_rollout_soft", "train_rollout_hard",
    "train_passed", "train_failed", "train_total",
    "selection_hard", "selection_soft", "selection_gate",
    "best_score", "current_score",
    "gate_action", "gate_reason",
    "edit_count", "patch_count", "reason",
    "slow_update_gate", "test_score", "test_hard",
]


def _write_csv(points: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for p in points:
            writer.writerow({k: p.get(k, "") for k in _CSV_COLUMNS})


_LOG_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_RE_INIT = re.compile(
    r"\[M4\] 初始评分: hard=([\d.]+) soft=([\d.]+).*?gate=([\d.]+)",
)
_RE_ROLLOUT = re.compile(
    r"\[M4\] step=(\d+) rollout: avg=([\d.]+) passed=(\d+) failed=(\d+)",
)
_RE_EVAL = re.compile(
    r"\[M4\] evaluate: hard=([\d.]+) soft=([\d.]+).*?gate=([\d.]+), best=([\d.]+)",
)
_RE_GATE = re.compile(r"\[M4\] gate: .+ reason=(.+?)(?:\s+\[|$)")
_RE_SLOW_GATE = re.compile(r"\[M4\] slow update gate: (\S+) \((.+)\)")
_RE_COMPARISON = re.compile(
    r"\[SlowUpdate\] Comparison: improved=(\d+) regressed=(\d+)"
    r" persistent_fail=(\d+) stable_success=(\d+)",
)
_RE_TEST = re.compile(r"\[M4\] Test eval: score=([\d.]+) hard=([\d.]+) n=(\d+)")


def _resolve_run_log(optimization_dir: str, log_path: str | None) -> str | None:
    if log_path and os.path.isfile(log_path):
        return log_path
    run_root = os.path.dirname(optimization_dir)
    candidate = os.path.join(run_root, "logs", "run.log")
    return candidate if os.path.isfile(candidate) else None


def _load_rollout_summary(optimization_dir: str, step: int) -> dict | None:
    path = os.path.join(
        optimization_dir, "steps", f"step_{step:04d}", "rollout_summary.json",
    )
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_patch_count(optimization_dir: str, step: int) -> int:
    path = os.path.join(
        optimization_dir, "steps", f"step_{step:04d}", "reflect_patches.json",
    )
    if not os.path.isfile(path):
        return 0
    with open(path, encoding="utf-8") as f:
        patches = json.load(f)
    return len(patches) if isinstance(patches, list) else 0


def _load_gate_eval(optimization_dir: str, step: int) -> dict | None:
    path = os.path.join(
        optimization_dir, "steps", f"step_{step:04d}", "eval_results.json",
    )
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _rollout_metrics(
    summary: dict | None,
    *,
    avg_soft: float | None = None,
) -> dict[str, Any]:
    if summary:
        total = max(summary.get("total", 1), 1)
        passed = summary.get("passed", 0)
        failed = summary.get("failed", 0)
        soft = avg_soft if avg_soft is not None else passed / total
        return {
            "train_rollout_soft": round(soft, 4),
            "train_rollout_hard": round(passed / total, 4),
            "train_passed": passed,
            "train_failed": failed,
            "train_total": total,
        }
    return {}


def backfill_training_curve_from_run(
    optimization_dir: str,
    *,
    log_path: str | None = None,
) -> dict:
    """从已有 run 产物回填训练曲线（适用于曲线功能上线前的历史 run）。"""
    config_path = os.path.join(optimization_dir, "config.json")
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)
    gate_metric = config.get("gate_metric", "soft")

    history_path = os.path.join(optimization_dir, "history.json")
    history: list[dict] = []
    if os.path.isfile(history_path):
        with open(history_path, encoding="utf-8") as f:
            history = json.load(f)
    history_by_step = {h["step"]: h for h in history}

    runtime_path = os.path.join(optimization_dir, "runtime_state.json")
    best_step = 0
    if os.path.isfile(runtime_path):
        with open(runtime_path, encoding="utf-8") as f:
            best_step = json.load(f).get("best_step", 0)

    test_report: dict[str, Any] = {}
    test_path = os.path.join(optimization_dir, "test_report.json")
    if os.path.isfile(test_path):
        with open(test_path, encoding="utf-8") as f:
            test_report = json.load(f)

    log_file = _resolve_run_log(optimization_dir, log_path)
    if not log_file:
        raise FileNotFoundError(
            f"run.log not found for backfill (optimization_dir={optimization_dir})",
        )

    parsed = _parse_run_log(log_file)
    points: list[dict[str, Any]] = []
    best_score = parsed["init"]["gate"]
    current_score = best_score

    points.append({
        "ts": parsed["init"]["ts"],
        "event": "init",
        "step": 0,
        "epoch": 0,
        "selection_hard": round(parsed["init"]["hard"], 4),
        "selection_soft": round(parsed["init"]["soft"], 4),
        "selection_gate": round(parsed["init"]["gate"], 4),
        "best_score": round(best_score, 4),
        "current_score": round(current_score, 4),
    })

    epochs_by_num = {e["epoch"]: e for e in parsed["epochs"]}
    seen_epochs: set[int] = set()

    for step_info in parsed["steps"]:
        step = step_info["step"]
        epoch = step_info["epoch"]
        summary = _load_rollout_summary(optimization_dir, step)
        rollout_fields = _rollout_metrics(summary, avg_soft=step_info.get("avg_soft"))

        if step_info["kind"] == "skip":
            points.append({
                "ts": step_info["ts"],
                "event": "skip",
                "step": step,
                "epoch": epoch,
                "reason": "no_valid_edits",
                "patch_count": _load_patch_count(optimization_dir, step),
                **rollout_fields,
            })
        else:
            gate_eval = _load_gate_eval(optimization_dir, step) or {}
            hist = history_by_step.get(step, {})
            gate_action = gate_eval.get("action") or hist.get("gate_action", "reject")
            selection_hard = gate_eval.get("hard", hist.get("selection_score", 0.0))
            selection_soft = gate_eval.get("soft", selection_hard)
            selection_gate = hist.get("selection_score", selection_hard)
            gate_reason = step_info.get("gate_reason", "")
            edit_count = hist.get("edit_count", 0)

            if gate_action in ("accept", "accept_new_best"):
                best_score = max(best_score, selection_gate)
                current_score = selection_gate

            points.append({
                "ts": step_info["ts"],
                "event": "gate",
                "step": step,
                "epoch": epoch,
                **rollout_fields,
                "selection_hard": round(selection_hard, 4),
                "selection_soft": round(selection_soft, 4),
                "selection_gate": round(selection_gate, 4),
                "best_score": round(best_score, 4),
                "current_score": round(current_score, 4),
                "gate_action": gate_action,
                "gate_reason": gate_reason,
                "edit_count": edit_count,
                "patch_count": _load_patch_count(optimization_dir, step),
            })

        if epoch not in seen_epochs:
            epoch_info = epochs_by_num.get(epoch)
            if epoch_info:
                points.append({
                    "ts": epoch_info["ts"],
                    "event": "epoch_end",
                    "step": step,
                    "epoch": epoch,
                    "best_score": round(best_score, 4),
                    "current_score": round(current_score, 4),
                    **({
                        "slow_update_gate": epoch_info["slow_update_gate"],
                        "slow_update_reason": epoch_info["slow_update_reason"],
                    } if epoch_info.get("slow_update_gate") else {}),
                    **({
                        "comparison_pairs": epoch_info["comparison_pairs"],
                    } if epoch_info.get("comparison_pairs") else {}),
                })
                seen_epochs.add(epoch)

    for epoch_info in parsed["epochs"]:
        if epoch_info["epoch"] not in seen_epochs:
            points.append({
                "ts": epoch_info["ts"],
                "event": "epoch_end",
                "step": epoch_info["step"],
                "epoch": epoch_info["epoch"],
                "best_score": round(best_score, 4),
                "current_score": round(current_score, 4),
                **({
                    "slow_update_gate": epoch_info["slow_update_gate"],
                    "slow_update_reason": epoch_info["slow_update_reason"],
                } if epoch_info.get("slow_update_gate") else {}),
                **({
                    "comparison_pairs": epoch_info["comparison_pairs"],
                } if epoch_info.get("comparison_pairs") else {}),
            })

    if parsed.get("test"):
        test = parsed["test"]
        points.append({
            "ts": test["ts"],
            "event": "test",
            "test_score": round(test["score"], 4),
            "test_hard": round(test["hard"], 4),
            "test_n_items": test["n_items"],
            "best_score": round(best_score, 4),
        })

    os.makedirs(optimization_dir, exist_ok=True)
    jsonl_path = os.path.join(optimization_dir, CURVE_JSONL)
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for point in points:
            f.write(json.dumps(point, ensure_ascii=False) + "\n")

    summary = _build_summary(points, best_step=best_step, test_report=test_report or None)
    payload = {
        "schema_version": "1.0",
        "gate_metric": gate_metric,
        "points": points,
        "series": _build_series(points),
        "summary": summary,
        "backfilled": True,
    }
    json_path = os.path.join(optimization_dir, CURVE_JSON)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    _write_csv(points, os.path.join(optimization_dir, CURVE_CSV))
    logger.info(
        "[curve] backfilled %d points → %s, %s",
        len(points), CURVE_JSON, CURVE_CSV,
    )
    return payload


def _parse_run_log(log_path: str) -> dict[str, Any]:
    """从 run.log 提取 init / step / epoch / test 事件及时间戳。"""
    init: dict[str, Any] | None = None
    steps: list[dict[str, Any]] = []
    epochs: list[dict[str, Any]] = []
    test: dict[str, Any] | None = None

    current_step: dict[str, Any] | None = None
    current_epoch = 0
    pending_comparison: dict[str, int] | None = None
    pending_slow_gate: str | None = None
    pending_slow_reason: str | None = None

    with open(log_path, encoding="utf-8") as f:
        for line in f:
            ts_match = _LOG_TS.match(line)
            ts = ts_match.group(1) if ts_match else ""

            epoch_m = re.search(r"\[M4\] === Epoch (\d+)/\d+ ===", line)
            if epoch_m:
                current_epoch = int(epoch_m.group(1))

            init_m = _RE_INIT.search(line)
            if init_m:
                init = {
                    "ts": ts,
                    "hard": float(init_m.group(1)),
                    "soft": float(init_m.group(2)),
                    "gate": float(init_m.group(3)),
                }
                continue

            rollout_m = _RE_ROLLOUT.search(line)
            if rollout_m:
                current_step = {
                    "ts": ts,
                    "step": int(rollout_m.group(1)),
                    "epoch": current_epoch,
                    "avg_soft": float(rollout_m.group(2)),
                    "passed": int(rollout_m.group(3)),
                    "failed": int(rollout_m.group(4)),
                    "kind": "pending",
                }
                continue

            if current_step and "validate: 无有效编辑，跳过本 step" in line:
                current_step["kind"] = "skip"
                current_step["ts"] = ts or current_step["ts"]
                steps.append(current_step)
                current_step = None
                continue

            eval_m = _RE_EVAL.search(line)
            if eval_m and current_step:
                current_step["kind"] = "gate"
                current_step["selection_hard"] = float(eval_m.group(1))
                current_step["selection_soft"] = float(eval_m.group(2))
                current_step["selection_gate"] = float(eval_m.group(3))
                current_step["best_before"] = float(eval_m.group(4))
                continue

            gate_m = _RE_GATE.search(line)
            if gate_m and current_step and current_step.get("kind") == "gate":
                current_step["gate_reason"] = gate_m.group(1).strip()
                current_step["ts"] = ts or current_step["ts"]
                steps.append(current_step)
                current_step = None
                continue

            cmp_m = _RE_COMPARISON.search(line)
            if cmp_m:
                pending_comparison = {
                    "improved": int(cmp_m.group(1)),
                    "regressed": int(cmp_m.group(2)),
                    "persistent_fail": int(cmp_m.group(3)),
                    "stable_success": int(cmp_m.group(4)),
                }
                continue

            slow_m = _RE_SLOW_GATE.search(line)
            if slow_m:
                icon = slow_m.group(1)
                pending_slow_reason = slow_m.group(2).strip()
                if icon == "⭐" or icon.startswith("accept"):
                    pending_slow_gate = "accept_new_best"
                elif icon == "✗" or "no_improvement" in pending_slow_reason:
                    pending_slow_gate = "reject"
                else:
                    pending_slow_gate = "reject"
                continue

            if "[M4] === Meta Skill epoch" in line and current_epoch:
                epochs.append({
                    "ts": ts,
                    "step": steps[-1]["step"] if steps else 0,
                    "epoch": current_epoch,
                    **({
                        "slow_update_gate": pending_slow_gate,
                        "slow_update_reason": pending_slow_reason,
                    } if pending_slow_gate else {}),
                    **({
                        "comparison_pairs": pending_comparison,
                    } if pending_comparison else {}),
                })
                pending_comparison = None
                pending_slow_gate = None
                pending_slow_reason = None
                continue

            test_m = _RE_TEST.search(line)
            if test_m:
                test = {
                    "ts": ts,
                    "score": float(test_m.group(1)),
                    "hard": float(test_m.group(2)),
                    "n_items": int(test_m.group(3)),
                }

    if init is None:
        raise ValueError(f"init score not found in {log_path}")

    # Epoch 1 无 slow update / meta skill，用该 epoch 最后 step 时间补 epoch_end
    if (
        not any(e["epoch"] == 1 for e in epochs)
        and steps
        and steps[0]["epoch"] == 1
    ):
        first = steps[0]
        epochs.insert(0, {
            "ts": first["ts"],
            "step": first["step"],
            "epoch": 1,
        })
    epochs.sort(key=lambda e: (e["epoch"], e["ts"]))

    return {"init": init, "steps": steps, "epochs": epochs, "test": test}


# ── Plot (pure SVG, no extra deps) ──────────────────────────

_PLOT_COLORS = {
    "selection_gate": "#2563eb",
    "train_rollout_hard": "#d97706",
    "best_score": "#16a34a",
    "test_hard": "#9333ea",
    "grid": "#e5e7eb",
    "axis": "#6b7280",
    "accept": "#16a34a",
    "reject": "#dc2626",
    "skip": "#9ca3af",
}


def plot_training_curve(
    optimization_dir: str,
    *,
    output_path: str | None = None,
    title: str | None = None,
) -> str:
    """从 ``training_curve.json`` 生成 SVG 训练曲线图。"""
    json_path = os.path.join(optimization_dir, CURVE_JSON)
    if not os.path.isfile(json_path):
        raise FileNotFoundError(f"{CURVE_JSON} not found in {optimization_dir}")
    with open(json_path, encoding="utf-8") as f:
        payload = json.load(f)

    out = output_path or os.path.join(optimization_dir, CURVE_SVG)
    svg = _render_curve_svg(
        payload["points"],
        gate_metric=payload.get("gate_metric", "soft"),
        summary=payload.get("summary", {}),
        title=title,
    )
    with open(out, "w", encoding="utf-8") as f:
        f.write(svg)
    logger.info("[curve] plotted → %s", out)
    return out


def _prepare_plot_data(points: list[dict]) -> dict[str, Any]:
    selection: list[tuple[float, float]] = []
    train_hard: list[tuple[float, float]] = []
    best_by_step: dict[int, float] = {}
    markers: list[dict[str, Any]] = []
    test: tuple[float, float] | None = None
    max_step = 0

    for p in points:
        event = p.get("event", "")
        step = p.get("step")
        if step is None:
            step = 0
        if isinstance(step, int):
            max_step = max(max_step, step)

        if event == "init":
            selection.append((0.0, float(p["selection_gate"])))
            best_by_step[0] = float(p["best_score"])
        elif event == "gate":
            selection.append((float(step), float(p["selection_gate"])))
            train_hard.append((float(step), float(p["train_rollout_hard"])))
            best_by_step[int(step)] = float(p["best_score"])
            color = (
                _PLOT_COLORS["accept"]
                if p.get("gate_action") in ("accept", "accept_new_best")
                else _PLOT_COLORS["reject"]
            )
            markers.append({
                "x": float(step),
                "y": float(p["selection_gate"]),
                "color": color,
                "label": p.get("gate_action", "gate"),
                "shape": "circle",
            })
        elif event == "skip" and p.get("train_rollout_hard") is not None:
            train_hard.append((float(step), float(p["train_rollout_hard"])))
            markers.append({
                "x": float(step),
                "y": float(p["train_rollout_hard"]),
                "color": _PLOT_COLORS["skip"],
                "label": "skip",
                "shape": "diamond",
            })
        elif event == "epoch_end" and p.get("best_score") is not None:
            best_by_step[int(step)] = float(p["best_score"])
        elif event == "test":
            test = (float(max_step) + 1.0, float(p.get("test_hard", 0.0)))

    best = sorted((float(k), v) for k, v in best_by_step.items())
    return {
        "selection": selection,
        "train_hard": train_hard,
        "best": best,
        "markers": markers,
        "test": test,
        "max_step": max(max_step, 1),
    }


def _render_curve_svg(
    points: list[dict],
    *,
    gate_metric: str,
    summary: dict,
    title: str | None,
) -> str:
    data = _prepare_plot_data(points)
    width, height = 920, 520
    margin = {"top": 56, "right": 180, "bottom": 56, "left": 64}
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]

    x_max = data["max_step"] + (1.5 if data["test"] else 0.5)

    def x_pos(step: float) -> float:
        return margin["left"] + (step / x_max) * plot_w

    def y_pos(val: float) -> float:
        return margin["top"] + (1.0 - val) * plot_h

    def polyline(series: list[tuple[float, float]]) -> str:
        if not series:
            return ""
        pts = " ".join(f"{x_pos(x):.1f},{y_pos(y):.1f}" for x, y in series)
        return f'<polyline fill="none" points="{pts}"/>'

    lines: list[str] = []
    lines.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="system-ui, sans-serif">'
    )
    lines.append('<rect width="100%" height="100%" fill="#fafafa"/>')

    heading = title or "SkillOpt Training Curve"
    lines.append(
        f'<text x="{margin["left"]}" y="28" font-size="16" font-weight="600" fill="#111827">'
        f'{_svg_escape(heading)}</text>'
    )
    subtitle = (
        f"gate={gate_metric}  best={summary.get('best_score', 0):.3f} "
        f"@ step {summary.get('best_step', 0)}  "
        f"test={summary.get('test_hard', summary.get('test_score', 0)):.3f}"
    )
    lines.append(
        f'<text x="{margin["left"]}" y="46" font-size="11" fill="#6b7280">'
        f'{_svg_escape(subtitle)}</text>'
    )

    for i in range(0, 11):
        val = i / 10
        y = y_pos(val)
        lines.append(
            f'<line x1="{margin["left"]}" y1="{y:.1f}" x2="{width - margin["right"]}" '
            f'y2="{y:.1f}" stroke="{_PLOT_COLORS["grid"]}" stroke-width="1"/>'
        )
        lines.append(
            f'<text x="{margin["left"] - 8}" y="{y + 4:.1f}" font-size="10" '
            f'text-anchor="end" fill="{_PLOT_COLORS["axis"]}">{val:.1f}</text>'
        )

    for step in range(0, int(data["max_step"]) + 1):
        x = x_pos(step)
        lines.append(
            f'<line x1="{x:.1f}" y1="{margin["top"]}" x2="{x:.1f}" '
            f'y2="{height - margin["bottom"]}" stroke="{_PLOT_COLORS["grid"]}" '
            f'stroke-width="1" stroke-dasharray="3,3"/>'
        )
        lines.append(
            f'<text x="{x:.1f}" y="{height - margin["bottom"] + 20}" font-size="10" '
            f'text-anchor="middle" fill="{_PLOT_COLORS["axis"]}">{step}</text>'
        )
    if data["test"]:
        tx = x_pos(data["test"][0])
        lines.append(
            f'<text x="{tx:.1f}" y="{height - margin["bottom"] + 20}" font-size="10" '
            f'text-anchor="middle" fill="{_PLOT_COLORS["test_hard"]}">test</text>'
        )

    lines.append(
        f'<line x1="{margin["left"]}" y1="{height - margin["bottom"]}" '
        f'x2="{width - margin["right"]}" y2="{height - margin["bottom"]}" '
        f'stroke="{_PLOT_COLORS["axis"]}" stroke-width="1.5"/>'
    )
    lines.append(
        f'<line x1="{margin["left"]}" y1="{margin["top"]}" '
        f'x2="{margin["left"]}" y2="{height - margin["bottom"]}" '
        f'stroke="{_PLOT_COLORS["axis"]}" stroke-width="1.5"/>'
    )
    lines.append(
        f'<text x="{(margin["left"] + width - margin["right"]) / 2:.1f}" '
        f'y="{height - 12}" font-size="11" text-anchor="middle" fill="{_PLOT_COLORS["axis"]}">'
        f'step</text>'
    )
    lines.append(
        f'<text x="16" y="{(margin["top"] + height - margin["bottom"]) / 2:.1f}" '
        f'font-size="11" text-anchor="middle" fill="{_PLOT_COLORS["axis"]}" '
        f'transform="rotate(-90 16 {(margin["top"] + height - margin["bottom"]) / 2:.1f})">'
        f'score</text>'
    )

    series_styles = [
        ("best", data["best"], _PLOT_COLORS["best_score"], "2,4", "best score"),
        ("selection", data["selection"], _PLOT_COLORS["selection_gate"], None, f"selection ({gate_metric})"),
        ("train", data["train_hard"], _PLOT_COLORS["train_rollout_hard"], None, "train rollout (hard)"),
    ]
    for _name, series, color, dash, _label in series_styles:
        if not series:
            continue
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        lines.append(
            f'<g stroke="{color}" stroke-width="2.5"{dash_attr}>'
            f'{polyline(series)}</g>'
        )
        for x, y in series:
            lines.append(
                f'<circle cx="{x_pos(x):.1f}" cy="{y_pos(y):.1f}" r="4" '
                f'fill="white" stroke="{color}" stroke-width="2"/>'
            )

    if data["test"]:
        tx, ty = data["test"]
        cx, cy = x_pos(tx), y_pos(ty)
        lines.append(
            f'<polygon points="{cx:.1f},{cy - 6:.1f} {cx + 6:.1f},{cy:.1f} '
            f'{cx:.1f},{cy + 6:.1f} {cx - 6:.1f},{cy:.1f}" '
            f'fill="{_PLOT_COLORS["test_hard"]}" stroke="white" stroke-width="1.5"/>'
        )

    for m in data["markers"]:
        cx, cy = x_pos(m["x"]), y_pos(m["y"])
        color = m["color"]
        if m["shape"] == "diamond":
            lines.append(
                f'<polygon points="{cx:.1f},{cy - 5:.1f} {cx + 5:.1f},{cy:.1f} '
                f'{cx:.1f},{cy + 5:.1f} {cx - 5:.1f},{cy:.1f}" fill="{color}"/>'
            )
        else:
            lines.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" fill="{color}"/>')

    legend_x = width - margin["right"] + 16
    legend_y = margin["top"] + 8
    legend_items = [
        (_PLOT_COLORS["selection_gate"], None, f"selection ({gate_metric})"),
        (_PLOT_COLORS["train_rollout_hard"], None, "train rollout (hard)"),
        (_PLOT_COLORS["best_score"], "2,4", "best score"),
        (_PLOT_COLORS["test_hard"], None, "test (hard)"),
        (_PLOT_COLORS["accept"], None, "gate accept"),
        (_PLOT_COLORS["reject"], None, "gate reject"),
        (_PLOT_COLORS["skip"], None, "skip"),
    ]
    for i, (color, dash, label) in enumerate(legend_items):
        y = legend_y + i * 22
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        lines.append(
            f'<line x1="{legend_x}" y1="{y + 4}" x2="{legend_x + 24}" y2="{y + 4}" '
            f'stroke="{color}" stroke-width="2.5"{dash_attr}/>'
        )
        lines.append(
            f'<text x="{legend_x + 32}" y="{y + 8}" font-size="11" fill="#374151">'
            f'{_svg_escape(label)}</text>'
        )

    lines.append("</svg>")
    return "\n".join(lines)


def _svg_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )

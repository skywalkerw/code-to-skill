"""run_manifest 记录测试。"""
from __future__ import annotations

import json

from code_to_skill.cli.run_manifest import PipelineRunRecorder


def test_pipeline_run_recorder_writes_manifest(tmp_path):
    out = str(tmp_path / "run1")
    import os
    os.makedirs(out)
    rec = PipelineRunRecorder("run1", out, domain="test")
    rec.skip_phase("m2_docs", "no docs")
    rec.start_phase("m4_skillopt")
    rec.end_phase("m4_skillopt", metrics={"best_score": 0.9})
    rec.set_summary(best_score=0.9)
    rec.finalize()
    path = rec.write()
    assert path.endswith("run_manifest.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["run_id"] == "run1"
    assert len(data["phases"]) == 2
    assert data["summary"]["best_score"] == 0.9

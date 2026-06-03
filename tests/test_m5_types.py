"""M5 + M6 集成测试。"""
import pytest
from pathlib import Path
from click.testing import CliRunner

from code_to_skill.model_gateway.types import InteractionRequest, InteractionResponse, ModelResponse
from code_to_skill.model_gateway.backends.mock import MockReplayBackend
from code_to_skill.model_gateway.router import Router
from code_to_skill.cli.types import RunManifest, RunState, RunStatus, StepInternal, ModuleEvent
from code_to_skill.cli.config_loader import ProjectConfig, RepoSource, DocSource


class TestM5Types:
    def test_interaction_request_defaults(self):
        req = InteractionRequest(role="extractor")
        assert req.schema_version == "1.0"
        assert req.stage == ""
        assert req.messages == []

    def test_model_response(self):
        resp = ModelResponse(
            request_id="req-001",
            backend_id="mock",
            model="gpt-4",
            content='{"key": 1}',
        )
        assert resp.schema_version == "1.0"
        assert resp.backend_type == "llm_api"


class TestM5Mock:
    def test_healthcheck(self):
        backend = MockReplayBackend("mock-1", fixture_dir="/nonexistent")
        status = backend.healthcheck()
        assert status.healthy is True

    def test_invoke_no_fixtures(self):
        backend = MockReplayBackend("mock-1", fixture_dir="/nonexistent")
        req = InteractionRequest(role="optimizer")
        resp = backend.invoke(req)
        assert resp.status == "ok"
        assert '"mock": true' in resp.content


class TestM5Router:
    def test_resolve(self):
        router = Router(
            route_config={"optimizer": {"primary": "dashscope", "fallback": ["azure"]}},
            backends={},
        )
        candidates = router.resolve("optimizer")
        assert candidates == ["dashscope", "azure"]


class TestM6Types:
    def test_run_manifest(self):
        m = RunManifest(run_id="test-001")
        assert m.schema_version == "1.0"
        assert m.run_id == "test-001"

    def test_run_state(self):
        s = RunState(run_id="test-001", status=RunStatus.running)
        assert s.status == RunStatus.running

    def test_step_internal(self):
        si = StepInternal(step=3, phase="rollout", rollout_completed=18, rollout_total=40)
        assert si.step == 3
        assert si.phase == "rollout"

    def test_event(self):
        e = ModuleEvent(module="code_graph", event="started", message="Parsing files")
        assert e.schema_version == "1.0"
        assert e.module == "code_graph"


class TestM6ConfigLoader:
    def test_doc_source(self):
        d = DocSource(id="test-doc", path="kb/test.md", type="markdown")
        assert d.provider == "local_file"

    def test_repo_source(self):
        r = RepoSource(id="fineract", path="/tmp/fineract", ref="develop",
                       include=["src/**"], exclude=["**/test/**"])
        assert r.include == ["src/**"]
        assert r.ref == "develop"


class TestM6CLI:
    def test_init(self, tmp_path):
        from code_to_skill.cli.main import init as _init_cmd
        runner = CliRunner()
        workspace = str(tmp_path / "test-proj")
        result = runner.invoke(_init_cmd, ["--workspace", workspace, "--domain", "fintech"])
        assert result.exit_code == 0
        assert Path(workspace, "project.yaml").exists()

    def test_config_validate_missing(self):
        from code_to_skill.cli.main import config_validate
        runner = CliRunner()
        result = runner.invoke(config_validate, ["--config-path", "/nonexistent/project.yaml"])
        assert result.exit_code == 1

    def test_run_all_dry_run(self, tmp_path):
        from code_to_skill.cli.main import run_all
        runner = CliRunner()
        result = runner.invoke(run_all, ["--config-path", "/nonexistent/project.yaml", "--dry-run"])
        # dry-run with invalid config should fail
        assert result.exit_code != 0

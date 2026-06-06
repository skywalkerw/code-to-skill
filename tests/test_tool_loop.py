"""tool_loop 多轮调用与 synthesis 回合测试。"""
from __future__ import annotations

from code_to_skill.model_provider.tool_loop import invoke_with_tool_loop
from code_to_skill.model_provider.types import InteractionRequest, ModelResponse


class _FakeHandler:
    definitions = [{"type": "function", "function": {"name": "search", "parameters": {}}}]

    def execute(self, tool_call: dict) -> str:
        return "found: AccountingProcessor.java"


class _FakeBackend:
    def __init__(self):
        self.calls: list[InteractionRequest] = []
        self._round = 0

    def invoke(self, request: InteractionRequest) -> ModelResponse:
        self.calls.append(request)
        self._round += 1
        if request.tools:
            return ModelResponse(
                request_id=request.request_id,
                backend_id="fake",
                model="fake",
                content="",
                tool_calls=[{
                    "id": f"tc{self._round}",
                    "type": "function",
                    "function": {"name": "search", "arguments": "{}"},
                }],
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            )
        return ModelResponse(
            request_id=request.request_id,
            backend_id="fake",
            model="fake",
            content="## 会计凭证\n| 借贷 | 科目 | 金额 |\n| 借 | 库存 | 100 |",
            usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        )


def test_tool_loop_synthesis_after_max_rounds():
    backend = _FakeBackend()
    request = InteractionRequest(
        role="target",
        stage="rollout",
        messages=[{"role": "user", "content": "generate voucher"}],
        max_output_tokens=1024,
    )
    resp = invoke_with_tool_loop(backend, request, _FakeHandler(), max_rounds=3)
    assert "会计凭证" in resp.content
    assert len(backend.calls) == 4  # 3 tool rounds + 1 synthesis
    assert backend.calls[-1].tools == []
    assert any("tool-call limit" in m.get("content", "") for m in backend.calls[-1].messages)

from types import SimpleNamespace

import pytest

from mini_coding_agent import agent, model_api


class FakeClient:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def create(self, **_):
        self.calls += 1
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(choices=[SimpleNamespace(message=outcome)])


def test_retry_starts_at_five_seconds_for_each_model_call(monkeypatch):
    client = FakeClient([
        RuntimeError("429"), RuntimeError("temporary"), "first",
        RuntimeError("429"), "second",
    ])
    delays = []
    monkeypatch.setattr(model_api, "OpenAI", lambda **kwargs: client)
    monkeypatch.setattr(model_api.time, "sleep", delays.append)
    monkeypatch.setitem(model_api.PROVIDERS[model_api.PROVIDER], "api_key", "test-key")

    assert model_api.call_model([{"role": "user", "content": "one"}]) == "first"
    assert model_api.call_model([{"role": "user", "content": "two"}]) == "second"
    assert delays == [5, 6, 5]
    assert client.calls == 5


def test_retry_exhaustion_stops_after_six_retries(monkeypatch):
    client = FakeClient([RuntimeError("429")] * 7)
    delays = []
    monkeypatch.setattr(model_api, "OpenAI", lambda **kwargs: client)
    monkeypatch.setattr(model_api.time, "sleep", delays.append)
    monkeypatch.setitem(model_api.PROVIDERS[model_api.PROVIDER], "api_key", "test-key")

    with pytest.raises(model_api.ModelCallError, match="after 7 attempts"):
        model_api.call_model([{"role": "user", "content": "test"}])

    assert client.calls == 7
    assert delays == [5, 6, 7, 8, 9, 10]


@pytest.mark.parametrize("failure_stage", ["compression", "main_call"])
def test_agent_ends_after_model_retry_exhaustion(monkeypatch, failure_stage):
    def fail(*_, **__):
        raise model_api.ModelCallError("all attempts failed")

    if failure_stage == "compression":
        monkeypatch.setattr(agent, "compress_history", fail)
    else:
        monkeypatch.setattr(agent, "compress_history", lambda **_: None)
        monkeypatch.setattr(agent, "call_model", fail)

    result = agent.run_agent("test task")
    assert result == "Agent stopped: all attempts failed"

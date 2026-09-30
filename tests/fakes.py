"""테스트용 가짜 Anthropic 클라이언트."""

from types import SimpleNamespace


class FakeMessages:
    def __init__(self, owner):
        self.owner = owner

    def _respond(self, method, kwargs):
        self.owner.calls.append((method, kwargs))
        result = self.owner.responses.pop(0) if self.owner.responses else self.owner.default
        if isinstance(result, Exception):
            raise result
        if callable(result):
            result = result(kwargs)
        return result

    def parse(self, **kwargs):
        return self._respond("parse", kwargs)

    def create(self, **kwargs):
        return self._respond("create", kwargs)


class FakeClient:
    def __init__(self, *responses, default=None):
        self.calls = []
        self.responses = list(responses)
        self.default = default
        self.beta = SimpleNamespace(messages=FakeMessages(self))


def parsed(obj, stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=None,
        parsed_output=obj,
        content=[],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0),
    )


def text_response(text, stop_reason="end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, stop_details=None, content=[SimpleNamespace(type="text", text=text)])

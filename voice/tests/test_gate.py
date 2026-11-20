import httpx
import pytest

from canopy_voice.config import DEFAULT_NO, DEFAULT_YES
from canopy_voice.gate import Gate, Policy, canonical, fetch_policy, verdict


@pytest.mark.parametrize("said,want", [
    ("Yes", "yes"), ("yeah go ahead", "yes"), ("OK, send it.", "yes"), ("Yes please do", "yes"),
    ("no", "no"), ("yes, but don't send it", "no"), ("wait", "no"), ("cancel that", "no"),
    ("what's the weather tomorrow", "other"), ("", "other"),
    ("yes and also tell me the weather tomorrow and then the news please", "other"),
])
def test_verdict(said, want):
    assert verdict(said, DEFAULT_YES, DEFAULT_NO) == want


class Params:
    def __init__(self, args):
        self.arguments, self.results = args, []

    async def result_callback(self, r):
        self.results.append(r)


async def test_a_held_call_runs_only_after_a_spoken_yes_with_the_same_arguments():
    ran = []

    async def handler(p):
        ran.append(p.arguments)
        await p.result_callback("sent")

    gate = Gate(Policy({"mail__send": True, "mail__read": False}, "test"))
    send = gate.wrap("mail__send", handler)
    assert gate.wrap("mail__read", handler) is handler

    p = Params({"to": "sam", "body": "hi"})
    await send(p)
    assert ran == [] and "NOT DONE" in p.results[0]
    assert gate.held == canonical("mail__send", {"body": "hi", "to": "sam"})

    gate.hear("yes", DEFAULT_YES, DEFAULT_NO)
    await send(Params({"to": "someone-else", "body": "hi"}))   # different arguments: held again, not run
    assert ran == []

    gate.hear("yes", DEFAULT_YES, DEFAULT_NO)
    await send(Params({"body": "hi", "to": "someone-else"}))
    assert ran == [{"body": "hi", "to": "someone-else"}]
    await send(Params({"body": "hi", "to": "someone-else"}))   # an approval is spent once
    assert len(ran) == 1


async def test_anything_but_yes_drops_the_held_call():
    gate = Gate(Policy({}, "test"))
    await gate.wrap("x__y", None)(Params({}))
    assert "declined" in gate.hear("no thanks", DEFAULT_YES, DEFAULT_NO)
    assert gate.held is None and gate.approved is None
    await gate.wrap("x__y", None)(Params({}))
    assert "dropped" in gate.hear("what time is it", DEFAULT_YES, DEFAULT_NO)
    assert gate.approved is None


def test_a_tool_the_policy_does_not_list_needs_confirmation():
    assert Policy({}, "x").needs("anything") is True


async def test_policy_is_read_from_canopy_and_fails_closed():
    def ok(request):
        assert request.headers["authorization"] == "Bearer pol"
        assert request.url.path == "/v1/gateway/groups/voice_alex/policy"
        return httpx.Response(200, json={"group": "voice_alex", "tools": [{"name": "a__b", "confirm": False}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(ok)) as c:
        p = await fetch_policy("http://api:8090", "pol", "voice_alex", c)
    assert p.needs("a__b") is False and p.source == "canopy"

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401))) as c:
        p = await fetch_policy("http://api:8090", "bad", "voice_alex", c)
    assert p.needs("a__b") is True and "unreadable" in p.source

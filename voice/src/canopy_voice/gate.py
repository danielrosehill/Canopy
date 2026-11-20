"""Confirmation enforced in code, not in the prompt.

A tool the group policy marks `confirm` is not run when the model first calls it. The call is held,
and the model is told to read it back and ask. The held call runs only if the speaker's next
utterance is a yes, judged here from the transcript by word lists, never by the model, and the model
then calls the same tool with the same arguments. Anything else drops it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

import httpx


def canonical(name: str, args: dict | None) -> str:
    return f"{name} {json.dumps(args or {}, sort_keys=True, ensure_ascii=False)}"


def _phrases_in(text: str, phrases: list[str]) -> bool:
    t = f" {text} "
    return any(f" {p} " in t for p in phrases)


def verdict(utterance: str, yes: list[str], no: list[str], max_words: int = 8) -> str:
    """'yes', 'no' or 'other' for what the speaker said after a read-back. A yes has to be short and
    carry no negation, so "yes, but don't send it" is not a yes."""
    text = re.sub(r"[^\w' ]+", " ", utterance.lower().replace("’", "'"))
    text = " ".join(text.split())
    if not text:
        return "other"
    if _phrases_in(text, no):
        return "no"
    if _phrases_in(text, yes) and len(text.split()) <= max_words:
        return "yes"
    return "other"


@dataclass
class Policy:
    """Which tools need confirmation. A tool the policy does not list needs it."""
    confirm: dict[str, bool]
    source: str

    def needs(self, tool: str) -> bool:
        return self.confirm.get(tool, True)


async def fetch_policy(policy_url: str, token: str, group: str, client: httpx.AsyncClient | None = None) -> Policy:
    """The group's policy from Canopy. If it cannot be read, every tool needs confirmation."""
    if not policy_url:
        return Policy({}, "no policy_url configured: every tool needs confirmation")
    own = client is None
    client = client or httpx.AsyncClient(timeout=10)
    try:
        r = await client.get(f"{policy_url}/v1/gateway/groups/{group}/policy",
                             headers={"Authorization": f"Bearer {token}"} if token else {})
        r.raise_for_status()
        return Policy({t["name"]: bool(t["confirm"]) for t in r.json()["tools"]}, "canopy")
    except (httpx.HTTPError, KeyError, ValueError) as e:
        return Policy({}, f"policy unreadable ({type(e).__name__}: {e}): every tool needs confirmation")
    finally:
        if own:
            await client.aclose()


HELD = ("NOT DONE. This action needs the speaker's explicit yes first. In one short sentence, say exactly "
        "what you are about to do (who, what, when) and ask them to confirm. Say nothing else. If they say "
        "yes, call {name} again with exactly the same arguments.")


class Gate:
    """Per conversation: one held call, and the call the speaker approved for this turn."""

    def __init__(self, policy: Policy):
        self.policy = policy
        self.held: str | None = None
        self.approved: str | None = None
        self.log: list[dict] = []

    def hear(self, utterance: str, yes: list[str], no: list[str], max_words: int = 8) -> str | None:
        """Called with each new utterance before the model sees it. Returns a note for the model."""
        self.approved = None
        if self.held is None:
            return None
        held, self.held = self.held, None
        v = verdict(utterance, yes, no, max_words)
        self.log.append({"held": held, "verdict": v})
        if v == "yes":
            self.approved = held
            return "[The speaker confirmed the held action. Call it now with the same arguments.]"
        if v == "no":
            return "[The speaker declined the held action. It was not done. Acknowledge briefly.]"
        return "[The held action was dropped: the speaker did not confirm it. Do not call it unless they ask again.]"

    def wrap(self, name: str, handler):
        """A Pipecat function handler that runs `handler` only when the policy and the speaker allow it."""
        if not self.policy.needs(name):
            return handler

        async def gated(params):
            key = canonical(name, params.arguments)
            if self.approved == key:
                self.approved = None
                self.log.append({"ran": key})
                return await handler(params)
            self.held = key
            self.log.append({"held": key})
            await params.result_callback(HELD.format(name=name))
        return gated

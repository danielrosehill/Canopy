"""Conversations: a Pipecat pipeline per turn over a kept LLM context, with the speaker's group tools
behind the confirmation gate."""
from __future__ import annotations

import asyncio
import fnmatch
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger
from mcp.client.session_group import StreamableHttpParameters
from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
from pipecat.adapters.llm.services.anthropic_adapter import AnthropicLLMAdapter
from pipecat.frames.frames import (EndFrame, ErrorFrame, Frame, FunctionCallResultFrame, FunctionCallsStartedFrame,
                                   LLMFullResponseEndFrame, LLMMessagesAppendFrame, LLMTextFrame)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.mcp_service import MCPClient
from pipecat.workers.runner import WorkerRunner

from .config import Config, Speaker
from .gate import Gate, Policy, fetch_policy


TOOL_SEARCH = {"type": "tool_search_tool_bm25_20251119", "name": "tool_search_tool_bm25"}


class DeferringAdapter(AnthropicLLMAdapter):
    """Claude's tool search for a wide group. Every tool's definition is still sent, but a tool marked
    `deferred` gets `defer_loading: true`, so Claude only loads it after finding it with the search tool,
    which goes first. The search runs on Anthropic's side: its blocks (server_tool_use,
    tool_search_tool_result) never reach a handler, and Pipecat leaves them out of the kept context. That
    is accepted by the API (checked 2026-11-21: the follow-up with only tool_use + tool_result succeeds, and
    the next turn calls the found tool again without searching).

    Thinking blocks are dropped from the assistant messages sent back. Pipecat keeps them but not the search
    blocks around them, and the API refuses a latest assistant message whose thinking block lost its
    neighbours ("thinking or redacted_thinking blocks in the latest assistant message cannot be modified",
    seen 2026-11-21 on a long fx tool loop). An assistant message with no thinking at all is accepted."""

    async def get_llm_invocation_params(self, context, *args, **kwargs):
        params = await super().get_llm_invocation_params(context, *args, **kwargs)
        if any(getattr(t, "deferred", False) for t in getattr(context.tools, "standard_tools", None) or []):
            params["messages"] = drop_thinking(params["messages"])
        return params

    def to_provider_tools_format(self, tools_schema: ToolsSchema) -> list[dict]:
        tools = super().to_provider_tools_format(tools_schema)
        deferred = {t.name for t in tools_schema.standard_tools if getattr(t, "deferred", False)}
        if not deferred:
            return tools
        for t in tools:
            if t["name"] in deferred:
                t["defer_loading"] = True
        return [dict(TOOL_SEARCH), *tools]


def drop_thinking(messages: list) -> list:
    out = []
    for m in messages:
        content = m.get("content")
        if m.get("role") == "assistant" and isinstance(content, list):
            kept = [b for b in content if (b.get("type") if isinstance(b, dict) else getattr(b, "type", None))
                    not in ("thinking", "redacted_thinking")]
            m = {**m, "content": kept or [{"type": "text", "text": "..."}]}
        out.append(m)
    return out


class SearchingAnthropicLLMService(AnthropicLLMService):
    adapter_class = DeferringAdapter


class GroupTools:
    """One gateway group: its MCP client, its tool schemas and its confirmation policy."""

    def __init__(self, cfg: Config, group: str):
        self.cfg, self.group = cfg, group
        headers = {"Authorization": f"Bearer {cfg.gateway_token}"} if cfg.gateway_token else None
        self.mcp = MCPClient(StreamableHttpParameters(url=f"{cfg.gateway_url}/v0/groups/{group}/mcp", headers=headers))
        self.schema: ToolsSchema | None = None
        self.policy = Policy({}, "not loaded")
        self.loaded_at = 0.0

    async def load(self) -> None:
        self.schema = await self.mcp.tools()
        self.policy = await fetch_policy(self.cfg.policy_url, self.cfg.policy_token, self.group)
        self.loaded_at = time.time()
        held = sum(1 for t in self.schema.standard_tools if self.policy.needs(t.name))
        loaded = self.cfg.tool_search_loaded
        search = "off" if loaded is None else \
            f"{sum(any(fnmatch.fnmatch(t.name, p) for p in loaded) for t in self.schema.standard_tools)} loaded"
        logger.info(f"group {self.group}: {len(self.schema.standard_tools)} tools, {held} need confirmation "
                    f"(policy: {self.policy.source}), tool search: {search}")

    def kept_open(self, handler):
        """Pipecat's MCP handlers carry a cleanup that closes the connection when the LLM service that ran
        them is torn down. A pipeline here lasts one turn and the connection is shared by every turn, so
        the plain function below hides that cleanup. Found 2026-11-20: after a turn that ran a tool, the
        next tool call failed with "MCPClient is not connected". A call that finds the connection closed
        anyway (a gateway restart) opens it again and runs once more."""
        async def call(params):
            try:
                await handler(params)
            except RuntimeError as e:
                if "not connected" not in str(e):
                    raise
                logger.warning(f"group {self.group}: MCP connection was closed, opening it again")
                await self.mcp.start()
                await handler(params)
        return call

    def gated(self, gate: Gate) -> ToolsSchema:
        """The group's tools with each handler behind this conversation's gate, in a stable order so the
        prompt cache holds. With tool search on, a tool no `tool_search.loaded` pattern names is deferred."""
        loaded = self.cfg.tool_search_loaded
        tools = []
        for t in sorted(self.schema.standard_tools, key=lambda t: t.name):
            copy = type(t)(name=t.name, description=t.description, properties=t.properties,
                           required=t.required, handler=gate.wrap(t.name, self.kept_open(t.handler)))
            copy.deferred = loaded is not None and not any(fnmatch.fnmatch(t.name, p) for p in loaded)
            tools.append(copy)
        return ToolsSchema(standard_tools=tools)


@dataclass
class Conversation:
    id: str
    speaker: Speaker
    context: LLMContext
    gate: Gate
    touched: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class _Collect(FrameProcessor):
    """Gathers the reply and the tool calls of one turn, and says when the turn is over: a response
    ends with no tool call outstanding."""

    def __init__(self):
        super().__init__()
        self.text: list[str] = []
        self.calls: list[dict] = []
        self.errors: list[str] = []
        self.outstanding = 0
        self.done = asyncio.Event()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, FunctionCallsStartedFrame):
            self.outstanding += len(frame.function_calls)
            self.text.clear()   # words before a tool call are a preamble, not the answer
        elif isinstance(frame, FunctionCallResultFrame):
            self.outstanding = max(0, self.outstanding - 1)
            self.calls.append({"tool": frame.function_name, "arguments": frame.arguments,
                               "result": str(frame.result)[:500]})
        elif isinstance(frame, LLMTextFrame):
            self.text.append(frame.text)
        elif isinstance(frame, ErrorFrame):
            self.errors.append(frame.error)
            self.done.set()
        elif isinstance(frame, LLMFullResponseEndFrame) and self.outstanding == 0:
            self.done.set()
        await self.push_frame(frame, direction)


class Engine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.groups: dict[str, GroupTools] = {}
        self.conversations: dict[str, Conversation] = {}
        self.tz = ZoneInfo(cfg.timezone)

    async def start(self) -> None:
        for g in sorted({s.group for s in self.cfg.speakers}):
            self.groups[g] = GroupTools(self.cfg, g)
            await self.groups[g].load()

    def _llm(self) -> AnthropicLLMService:
        settings = AnthropicLLMService.Settings(model=self.cfg.model, max_tokens=self.cfg.max_tokens,
                                                enable_prompt_caching=True, system_instruction=self.cfg.prompt)
        if self.cfg.effort:
            settings.effort = self.cfg.effort
        cls = SearchingAnthropicLLMService if self.cfg.tool_search_loaded is not None else AnthropicLLMService
        return cls(api_key=os.environ["ANTHROPIC_API_KEY"], settings=settings)

    def conversation(self, speaker: Speaker, conversation_id: str | None) -> Conversation:
        now = time.time()
        for k in [k for k, c in self.conversations.items() if now - c.touched > self.cfg.session_ttl_s]:
            del self.conversations[k]
        c = self.conversations.get(conversation_id or "")
        if c is None or c.speaker.id != speaker.id:
            group = self.groups[speaker.group]
            gate = Gate(group.policy)
            c = Conversation(uuid.uuid4().hex[:12], speaker, LLMContext(tools=group.gated(gate)), gate)
            self.conversations[c.id] = c
        c.touched = now
        return c

    def context_line(self, speaker: Speaker) -> str:
        now = datetime.now(self.tz)
        line = f"[Speaker: {speaker.name} (address as \"{speaker.address}\") | Local time: {now:%A %d %B %Y, %H:%M}"
        return line + (f" | {speaker.context}]" if speaker.context else "]")

    async def turn(self, speaker: Speaker, text: str, conversation_id: str | None = None,
                   timeout: float = 60) -> dict:
        c = self.conversation(speaker, conversation_id)
        async with c.lock:
            t0 = time.time()
            note = c.gate.hear(text, self.cfg.yes, self.cfg.no, self.cfg.max_confirm_words)
            content = "\n".join(x for x in (self.context_line(speaker), note, text) if x)
            collect = _Collect()
            agg = LLMContextAggregatorPair(c.context)
            worker = PipelineWorker(Pipeline([agg.user(), self._llm(), collect, agg.assistant()]),
                                    params=PipelineParams(enable_metrics=False))
            runner = WorkerRunner(handle_sigint=False)
            await runner.add_workers(worker)
            run = asyncio.create_task(runner.run())
            await worker.queue_frame(LLMMessagesAppendFrame(messages=[{"role": "user", "content": content}],
                                                            run_llm=True))
            try:
                await asyncio.wait_for(collect.done.wait(), timeout)
            except TimeoutError:
                collect.errors.append(f"no reply within {timeout:.0f} s")
            await worker.queue_frame(EndFrame())
            try:
                await asyncio.wait_for(run, 10)
            except TimeoutError:
                await worker.cancel()
            reply = "".join(collect.text).strip()
            if collect.errors and not reply:
                reply = "Sorry, something went wrong."
            return {"reply": reply, "conversation_id": c.id, "speaker": speaker.id, "tool_calls": collect.calls,
                    "held": c.gate.held, "errors": collect.errors, "seconds": round(time.time() - t0, 2)}

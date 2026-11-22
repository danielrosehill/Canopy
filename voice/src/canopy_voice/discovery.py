"""Tool discovery through Canopy, the way every other Canopy client finds tools.

The model gets only the `loaded` tools natively, plus the group's Canopy catalogue search
(`catalogue__search`, or a scoped catalogue's own search). Canopy ranks the result: hybrid keyword and
vector search, then the reranker (docs/architecture.md, Search). Whatever a search finds that is not loaded
is called through the built-in `call_tool`, the voice twin of `toolbox__call_tool`.

Two things differ from a plain toolbox call, and both keep voice safe:

- The speaker's group is still the allow-list. A search can rank tools from outside it; those are dropped
  from the result, and `call_tool` refuses them.
- The confirmation gate judges the real tool, not `call_tool`: a held call is keyed on the inner tool's
  name and arguments, exactly as a native call would be.

The search result is also given each found tool's input schema (from the group's own tools/list), so the
model can call it straight away instead of reading the tool first: one round trip fewer on every lookup.
"""
from __future__ import annotations

import json

CALL_TOOL = "call_tool"

CALL_TOOL_DOC = (
    "Call a tool that the catalogue search found but that is not in your tool list. name: the tool's name "
    "exactly as the search gave it (without the 'tool:' prefix). arguments: an object matching that tool's "
    "input_schema from the search result. The result is the tool's own result, and its confirmation rules apply.")
CALL_TOOL_PROPS = {
    "name": {"type": "string", "description": "The tool's name, e.g. donetick__list_chores"},
    "arguments": {"type": "object", "description": "The tool's arguments, as its input_schema describes"},
}

SEARCH_NOTE = ("Tools marked call 'call_tool' are not in your tool list: call call_tool with that name and "
               "arguments matching input_schema. Only tools you may use are listed.")


def tool_name(record_id: str) -> str | None:
    """'tool:server__name' -> 'server__name'; anything else (a skill, a plugin) -> None."""
    return record_id[5:] if isinstance(record_id, str) and record_id.startswith("tool:") else None


def rewrite_search(text: str, group: dict[str, dict], loaded: set[str]) -> str:
    """The catalogue's search result as the speaker should see it.

    `group`: every tool in the speaker's group, name -> {"properties", "required"}. `loaded`: the names the
    model has natively. A tool record outside the group is dropped. One in the group gets `call` set to its
    own name when loaded, else to call_tool plus its input_schema. Skills and other records pass through.
    A result that is not the catalogue's JSON is returned as it is.
    """
    try:
        doc = json.loads(text)
    except (TypeError, ValueError):
        return text
    if not isinstance(doc, dict):
        return text
    found_unloaded = False
    for key in ("results", "tools"):
        items = doc.get(key)
        if not isinstance(items, list):
            continue
        kept = []
        for item in items:
            name = tool_name(item.get("id")) if isinstance(item, dict) else None
            if name is None:
                kept.append(item)
                continue
            if name not in group:
                continue
            if name in loaded:
                item = {**item, "call": name}
            else:
                found_unloaded = True
                item = {**item, "call": CALL_TOOL, "input_schema": {
                    "type": "object", "properties": group[name].get("properties") or {},
                    "required": group[name].get("required") or []}}
            kept.append(item)
        if kept:
            doc[key] = kept
        else:
            doc.pop(key, None)
    if found_unloaded:
        doc["note"] = SEARCH_NOTE
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"))


def check_call(name, arguments, group: set[str], blocked: set[str]) -> str | None:
    """Why a call_tool request can't run, or None if it can."""
    if not isinstance(name, str) or not name:
        return "call_tool needs `name`: the tool's name from the search result."
    if name.startswith("tool:"):
        name = name[5:]
    if name in blocked:
        return f"{name} is called directly, not through call_tool."
    if name not in group:
        return (f"{name} is not available to this speaker. Search the catalogue again and use a tool it lists, "
                "or say it can't be done.")
    if arguments is not None and not isinstance(arguments, dict):
        return "call_tool `arguments` must be an object."
    return None

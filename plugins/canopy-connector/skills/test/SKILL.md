---
name: test
description: Test the connection to the Canopy gateway and report green or red, with latency. Use on "test the connection", "is the gateway up", "is Canopy working", or "run the connector test".
---

# Test the connection

1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/skills/test/scripts/check.py"`. It needs `CANOPY_MCP_URL`, and
   `CANOPY_TOKEN` if the server has auth. To find particular tools as well, add `--expect tool__one,tool__two`.
2. Call `catalogue__health` from this session. The script proves the network path. This proves the session has
   the tools.
3. Report in a line or two, leading with the script's last line: `GREEN`, `AMBER` or `RED`. Give the latencies.

On `RED`, say which step failed. If the script passes and this session has no tools, reconnect the server with `/mcp`.

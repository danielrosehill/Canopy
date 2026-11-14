# Parallel art

Other projects working on the same problems as Canopy: aggregating MCP servers behind one endpoint, loading tools
only when they're needed, finding skills, and self-hosting it all. Use this list to see what else exists, borrow
ideas and check Canopy's claims against the field. It isn't a ranking.

**Last checked: 2026-11-14.** Every URL was fetched on that date. Star counts come from GitHub on the same date.

Overlap tags:

- **AGG**: aggregates MCP servers
- **PTD**: progressive tool discovery (search, then load)
- **SKL**: finds skills
- **SH**: a self-hosted single endpoint

## Overlap with Canopy's skills claim

The README says nothing off the shelf did progressive discovery for skills. Canopy was started on that premise. As
of the date above, these projects come closest to it:

- [agentic-community/mcp-gateway-registry](https://github.com/agentic-community/mcp-gateway-registry): one
  governed gateway for MCP servers, agents and `SKILL.md` skills, with natural-language search across all of them.
  This is the nearest match to Canopy as a whole. It is aimed at organisations: Keycloak or Entra, and EKS, ECS or
  Compose.
- [Obot](https://github.com/obot-platform/obot): tool search across large virtual MCP servers, plus MCP and skills
  registries built on Git-backed catalogues.
- [K-Dense-AI/claude-skills-mcp](https://github.com/K-Dense-AI/claude-skills-mcp): vector search over Claude
  Agent Skills, loaded progressively. It handles skills only and doesn't aggregate MCP.
- Self-hosted skill registries such as [iflytek/skillhub](https://github.com/iflytek/skillhub) and
  [ZenlixAI/skill-dock](https://github.com/ZenlixAI/skill-dock) publish and find skills. They don't put skills
  behind the agent's single MCP endpoint.

Canopy's narrower claim still holds. It is one self-hosted endpoint that combines three things:

- hybrid BM25 and embedding search over Claude Code plugin marketplaces
- an MCP aggregator, used as-is
- thin clients that hold no credentials

## MCP gateways and aggregators

| Project | What it does | Overlap | Licence | Stars |
| --- | --- | --- | --- | --- |
| [MCPJungle](https://github.com/mcpjungle/MCPJungle) | Self-hosted MCP registry and gateway. Canopy runs it as its tool layer | AGG, SH | MPL-2.0 | 1.6k |
| [agentic-community/mcp-gateway-registry](https://github.com/agentic-community/mcp-gateway-registry) | Gateway and registry for MCP servers, agents and skills, with semantic and lexical search | AGG, PTD, SKL, SH | Apache-2.0 | 1.1k |
| [Obot](https://github.com/obot-platform/obot) | AI governance platform: MCP gateway, tool search, MCP and skills registries | AGG, PTD, SKL, SH | MIT | 1.2k |
| [ToolHive](https://github.com/stacklok/toolhive) | Runs MCP servers in isolated containers. Its MCP Optimizer does semantic tool search | AGG, PTD, SH | Apache-2.0 | 2.3k |
| [1MCP Agent](https://github.com/1mcp-app/agent) | Aggregates MCP servers into one. Optional lazy loading (`tool_list`, `tool_schema`, `tool_invoke`) | AGG, PTD, SH | Apache-2.0 | 0.5k |
| [Docker MCP Gateway](https://github.com/docker/mcp-gateway) | Runs containerised MCP servers behind one gateway. [Dynamic MCP](https://docs.docker.com/ai/mcp-catalog-and-toolkit/dynamic-mcp/) adds servers mid-session | AGG, PTD | MIT | 1.6k |
| [IBM ContextForge](https://github.com/IBM/mcp-context-forge) | Gateway, registry and proxy for MCP, A2A and REST behind one endpoint | AGG, SH | Apache-2.0 | 4.8k |
| [Jarvis Registry](https://github.com/ascending-llc/jarvis-registry) | MCP and A2A gateway with semantic search over servers and agents. Its "skills" are capability tags, not `SKILL.md` | AGG, PTD, SH | Apache-2.0 | 4.0k |
| [agentgateway](https://github.com/agentgateway/agentgateway) | Proxy for agents and MCP servers | AGG | Apache-2.0 | 6.2k |
| [Microsoft mcp-gateway](https://github.com/microsoft/mcp-gateway) | Reverse proxy that routes MCP sessions and manages servers on Kubernetes | AGG | MIT | 0.9k |
| [Unla](https://github.com/AmoyLab/Unla) | Turns existing APIs and MCP servers into MCP endpoints, with a management UI | AGG, SH | MIT | 2.3k |
| [Archestra](https://github.com/archestra-ai/archestra) | Enterprise platform: MCP registry, gateway, orchestrator and guardrails | AGG, SH | Not detected | 4.4k |
| [MetaMCP](https://github.com/metatool-ai/metamcp) | MCP aggregator, orchestrator and middleware in one Docker image. **Archived** | AGG, SH | MIT | 2.8k |
| [MCP Router](https://github.com/mcp-router/mcp-router) | Desktop MCP manager. **Archived**: development ended 2026-09-18 | AGG | Not detected | 2.1k |
| [MCP Hub](https://github.com/ravitemer/mcp-hub) | Central manager for MCP servers. Last push 2025-10-24 | AGG | MIT | 0.5k |
| [Magg](https://github.com/sitbon/magg) | MCP aggregator | AGG | AGPL-3.0 | 0.1k |
| [Lasso mcp-gateway](https://github.com/lasso-security/mcp-gateway) | Security gateway that orchestrates other MCP servers, extended through plugins | AGG | MIT | 0.4k |
| [tbxark/mcp-proxy](https://github.com/tbxark/mcp-proxy) | Many MCP servers behind one HTTP server | AGG, SH | MIT | 0.7k |
| [Bifrost](https://github.com/maximhq/bifrost) | LLM gateway that is also an MCP gateway | AGG | Apache-2.0 | 9.6k |
| [Portkey gateway](https://github.com/Portkey-AI/gateway) | LLM gateway with an MCP control plane | AGG | MIT | 13.4k |
| [mcpo](https://github.com/open-webui/mcpo) | Exposes MCP servers as OpenAPI endpoints | Adapter | MIT | 4.4k |
| [supergateway](https://github.com/supercorp-ai/supergateway) | Serves stdio MCP servers over HTTP and SSE | Transport | MIT | 3.0k |
| [sparfenyuk/mcp-proxy](https://github.com/sparfenyuk/mcp-proxy) | Bridges the Streamable HTTP and stdio transports | Transport | MIT | 2.8k |

## Tool search and loading on demand

| Project | What it does | Overlap |
| --- | --- | --- |
| [Anthropic tool search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool) | API feature: tools marked `defer_loading` load only when the model searches for them | PTD |
| [Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use) | Anthropic write-up on tool search, programmatic tool calling and tool examples | PTD |
| [Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp) | Tools as code APIs, loaded on demand instead of being described up front | PTD |
| [Composio](https://github.com/ComposioHQ/composio) | Over 1,000 toolkits, with tool search and managed auth (MIT, 31k stars) | PTD |
| [Pipedream MCP](https://pipedream.com/mcp) | Hosted MCP access to thousands of app integrations, with managed auth | Hosted tools |
| [MCP-Zero](https://github.com/xfey/MCP-Zero) | The agent asks for the tools it needs, and a retriever finds them. Code for the paper below | PTD |

## Skill and plugin discovery

| Project | What it does | Overlap | Licence | Stars |
| --- | --- | --- | --- | --- |
| [claude-skills-mcp](https://github.com/K-Dense-AI/claude-skills-mcp) | MCP server: vector search over Claude Agent Skills, with progressive loading | PTD, SKL | Apache-2.0 | 0.4k |
| [iflytek/skillhub](https://github.com/iflytek/skillhub) | Self-hosted skill registry: publish, version, search, RBAC | SKL, SH | Apache-2.0 | 5.4k |
| [skill-dock](https://github.com/ZenlixAI/skill-dock) | Self-hosted control plane to publish, find and install skills | SKL, SH | MIT | 0.6k |
| [ClawHub](https://github.com/openclaw/clawhub) | Skill and plugin registry for OpenClaw, with vector search | SKL | MIT | 9.6k |
| [Kamalnrf/claude-plugins](https://github.com/Kamalnrf/claude-plugins) | Registry and CLI for Claude Code plugins and skills, with a skill that finds other skills | SKL | None declared | 0.6k |
| [claude-skill-registry](https://github.com/majiayu000/claude-skill-registry) | Searchable catalogue of Claude Code skills | SKL | MIT | 0.7k |
| [vercel-labs/skills](https://github.com/vercel-labs/skills) | The `npx skills find` CLI and the [skills.sh](https://skills.sh) directory | SKL | MIT | 39k |
| [openskills](https://github.com/numman-ali/openskills) | Universal skills loader with progressive disclosure | SKL | Not detected | 10.8k |
| [skillz](https://github.com/intellectronica/skillz) | MCP server that exposes `SKILL.md` skills as tools | SKL | MIT | 0.4k |
| [tech-leads-club/agent-skills](https://github.com/tech-leads-club/agent-skills) | Validated skill registry for coding agents, with an MCP server | SKL | Not detected | 7.2k |
| [claude-code-templates](https://github.com/davila7/claude-code-templates) | Catalogue and CLI for skills, agents, hooks and MCP servers | SKL | MIT | 33.6k |
| [Smithery](https://smithery.ai) | MCP server and skills hub, now part of Arcade | Servers, SKL | AGPL-3.0 (CLI) | n/a |
| [Official MCP Registry](https://registry.modelcontextprotocol.io) | Community registry of MCP servers ([repo](https://github.com/modelcontextprotocol/registry)) | Servers | Other | 7.5k |
| [Docker MCP Registry](https://github.com/docker/mcp-registry) | Docker's catalogue of MCP servers | Servers | MIT | 0.6k |
| [Glama](https://glama.ai) | MCP server registry, inspector and gateway | Servers, AGG | Proprietary | n/a |
| [Agentic Resource Discovery](https://github.com/neuronto/agentic-resource-discovery) | Federated search across agent resource registries, with an open spec | Discovery | Apache-2.0 | 0.1k |
| [anthropics/skills](https://github.com/anthropics/skills) and the [Agent Skills spec](https://agentskills.io) | The official skills and the format | Source | n/a | n/a |

Directories without search, such as [MCP.so](https://mcp.so), [MCP Market](https://mcpmarket.com),
[awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers) and
[awesome-claude-skills](https://github.com/ComposioHQ/awesome-claude-skills), are where you find upstreams to add.
They don't discover anything at runtime.

## Research on tool retrieval

- [RAG-MCP](https://arxiv.org/abs/2505.03275): picks tools by retrieval to keep prompts small. This is the closest
  paper to Canopy's approach.
- [ScaleMCP](https://arxiv.org/abs/2505.06416): MCP tool retrieval that keeps itself in sync with the tool set.
- [MCP-Zero](https://arxiv.org/abs/2506.01056): the agent discovers tools actively, asking for what it needs.
- [ToolRet](https://arxiv.org/abs/2503.01763): a benchmark showing that general-purpose retrievers do poorly at
  finding tools.
- [Toolshed](https://arxiv.org/abs/2410.14594): fuses retrieval with tool knowledge bases, for large tool sets.
- [Tool2Vec](https://arxiv.org/abs/2409.02141): tool embeddings built from how the tools are used.
- [Gorilla](https://arxiv.org/abs/2305.15334) and [ToolLLM](https://arxiv.org/abs/2307.16789): retrievers over
  thousands of real APIs.

## Updating

Re-check each entry. Mark archived or dormant ones instead of removing them, since they still count as prior art.
Add new ones only after fetching their pages. Update the "Overlap with Canopy's skills claim" section first. Then
set **Last checked**.

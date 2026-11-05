#!/usr/bin/env python3
"""Build the Home Canopy and Cloud Canopy network diagrams.

Writes <name>.dot and <name>.pdf beside this file. Needs Graphviz: python3 docs/diagrams/build.py
Keep the Canopy stack in stack() in step with compose.yaml.
"""
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
FONT = "Helvetica"
LINK = {  # class: (colour, style, width, legend)
    "mcp":    ("#1b5e20", "solid", 1.8, "MCP over HTTP, with a token"),
    "ts":     ("#1565c0", "solid", 1.8, "Tailscale (WireGuard)"),
    "cf":     ("#e65100", "solid", 1.8, "Cloudflare: OAuth, Access, tunnel"),
    "oai":    ("#6a1b9a", "dashed", 1.5, "OpenAI tunnel"),
    "lan":    ("#7cb342", "bold", 2.6, "Local network: API or SSH"),
    "out":    ("#37474f", "solid", 1.2, "Outbound HTTPS"),
    "ctl":    ("#757575", "solid", 1.0, "Internal: control API, database"),
    "file":   ("#00838f", "solid", 1.6, "Files: S3 and presigned URLs"),
    "idea":   ("#c62828", "dashed", 1.8, "Not built: the anti-hairpin route"),
}
ZONE = {"internet": "#f5f5f5", "cf": "#fff3e0", "ts": "#e3f2fd", "site": "#e8f5e9", "host": "#f1f8e9",
        "stack": "#dcedc8", "cloud": "#e1f5fe", "oai": "#f3e5f5", "client": "#fafafa"}


def n(id, title, sub="", fill="white", dashed=False):
    sub = f'<BR/><FONT POINT-SIZE="13" COLOR="#455a64">{sub}</FONT>' if sub else ""
    style = "rounded,filled,dashed" if dashed else "rounded,filled"
    return f'{id} [label=<<B>{title}</B>{sub}>, style="{style}", fillcolor="{fill}"];'


def zone(id, label, fill, *body, dashed=False):
    style = "rounded,filled,dashed" if dashed else "rounded,filled"
    inner = "\n".join(body)
    return (f'subgraph cluster_{id} {{ label=<<B>{label}</B>>; labeljust=l; style="{style}"; '
            f'fillcolor="{ZONE[fill]}"; color="#90a4ae"; fontsize=18;\n{inner}\n}}')


def e(a, b, kind, label="", **kw):
    colour, style, width, _ = LINK[kind]
    attrs = {"color": colour, "style": style, "penwidth": width, "fontcolor": colour, **kw}
    if label:
        attrs["label"] = label
    return f"{a} -> {b} [" + ", ".join(f'{k}="{v}"' for k, v in attrs.items()) + "];"


def stack(zone_label):
    """The compose stack. Dashed nodes are opt-in profiles."""
    return zone("stack", zone_label, "stack",
        n("gateway", "gateway", "MCPJungle :8080<br/>/v0/groups/core/mcp<br/>/v0/groups/hosted/mcp", "#aed581"),
        n("catalogue", "catalogue", "skills index, BM25 + vectors"),
        n("toolbox", "toolbox", "long-tail tools on demand"),
        n("api", "api", "control API :8090<br/>scheduler, backup, staging"),
        n("db", "db", "Postgres + pgvector"),
        n("seed", "seed", "one-shot, applies config"),
        n("cli", "cli", "gh, aws, rclone, restic<br/>logins stay here"),
        n("ui", "ui", "web picker :8081<br/>profile: ui", dashed=True),
        n("storage", "storage", "Garage S3 :3900, 1-day expiry<br/>profile: storage", dashed=True),
        n("ollama", "ollama", "bge-m3 embeddings<br/>profile: search", dashed=True),
        n("cfd", "tunnel", "cloudflared, dials out<br/>profile: tunnel", dashed=True),
        n("tsc", "tailnet", "Tailscale sidecar<br/>profile: tailnet", dashed=True),
    )


def stack_links():
    return [
        e("gateway", "catalogue", "mcp", "core group"), e("gateway", "toolbox", "mcp", "core group"),
        e("toolbox", "gateway", "mcp", "calls upstreams", constraint="false"),
        e("gateway", "ui", "mcp", dir="back"),
        e("seed", "api", "ctl"), e("api", "gateway", "ctl", "admin REST", constraint="false"),
        e("api", "db", "ctl"), e("catalogue", "db", "ctl", constraint="false"), e("gateway", "db", "ctl", constraint="false"),
        e("catalogue", "api", "ctl", "embeddings", constraint="false"),
        e("api", "ollama", "ctl"),
        e("cfd", "gateway", "cf"), e("tsc", "gateway", "ts"),
    ]


def clients(with_home_device=False):
    own = [n("cc", "Claude Code", "canopy-connector plugin:<br/>one connection, find, test, /skill"),
           n("pick", "canopy-pick", "terminal skill picker"),
           n("webui", "Open WebUI, LibreChat", "self-run clients, bearer token")]
    if with_home_device:
        own.insert(1, n("athome", "Claude Code at home", "same connector, on the home network"))
    return [zone("own", "Your own devices", "client", *own),
            zone("hosted", "Hosted clients", "client",
                 n("claudeai", "claude.ai", "custom connector"), n("chatgpt", "ChatGPT", "app or plugin")),
            n("scripts", "Scripts and agents elsewhere", "can send CF-Access headers")]


def edge_services():
    return [zone("cf", "Cloudflare", "cf",
                 n("portal", "MCP server portal", "OAuth 2.1 + PKCE, per person"),
                 n("access", "Access", "service tokens"),
                 n("cfedge", "Tunnel edge", "private route to the gateway")),
            zone("tailnet", "Tailnet", "ts", n("mesh", "Tailscale mesh", "members only, nothing public"))]


def cloud_services():
    return zone("cloud", "Cloud services", "cloud",
        n("git", "Plugin marketplaces", "git repos, cloned and indexed"),
        n("apis", "Cloud APIs", "Gemini, image and video models"),
        n("saas", "Hosted MCP servers", "SaaS upstreams"),
        n("embed", "Embeddings and rerank", "hosted fallback, optional"),
        n("s3", "Off-site S3", "restic backups"))


def client_links():
    return [
        e("cc", "mesh", "ts"), e("pick", "mesh", "ts"), e("webui", "mesh", "ts"),
        e("mesh", "tsc", "ts", "MCP inside the tailnet"),
        e("claudeai", "portal", "cf", "OAuth"), e("chatgpt", "portal", "cf", "OAuth"),
        e("scripts", "access", "cf", "service token"),
        e("portal", "cfedge", "cf", "hosted group"), e("access", "cfedge", "cf"),
        e("cfedge", "cfd", "cf", "tunnel"),
        e("chatgpt", "oaitc", "oai", "retired in the source deployment"), e("oaitc", "gateway", "oai", "hosted group"),
    ]


def outbound_links():
    return [
        e("catalogue", "git", "out", "clone"), e("catalogue", "embed", "out", "rerank"), e("api", "embed", "out", "embed"),
        e("gateway", "saas", "mcp"), e("localmcp", "apis", "out", "tools call out"), e("cli", "apis", "out"),
        e("api", "s3", "out", "restic"),
        e("mesh", "api", "file", "stage a file: needs the API token"),
        e("api", "storage", "file", "stage"),
        e("storage", "localmcp", "file", "tool fetches by presigned URL", constraint="false"),
    ]


def legend(used):
    rows = "".join(f'<TR><TD ALIGN="LEFT"><FONT COLOR="{c}"><B>{"- - - -" if s == "dashed" else "━━━━"}</B></FONT></TD>'
                   f'<TD ALIGN="LEFT">{t}</TD></TR>' for k, (c, s, _, t) in LINK.items() if k in used)
    return ('legend [shape=plaintext, fontsize=15, label=<<TABLE BORDER="1" COLOR="#90a4ae" CELLBORDER="0" CELLSPACING="3" CELLPADDING="3">'
            f'<TR><TD COLSPAN="2" ALIGN="LEFT"><B>Key</B></TD></TR>{rows}'
            '<TR><TD ALIGN="LEFT">Dashed box</TD><TD ALIGN="LEFT">Opt-in compose profile, or optional</TD></TR></TABLE>>];')


def graph(title, status, body):
    head = f'''digraph G {{
rankdir=LR; compound=true; newrank=true; nodesep=0.55; ranksep=0.7; splines=true; pad=0.5; bgcolor=white;
labelloc=t; labeljust=l; fontname="{FONT}"; fontsize=20;
label=<<FONT POINT-SIZE="40"><B>{title}</B></FONT><BR ALIGN="LEFT"/><BR ALIGN="LEFT"/>{status}<BR ALIGN="LEFT"/><BR ALIGN="LEFT"/>>;
node [shape=box, fontname="{FONT}", fontsize=17, color="#546e7a", margin="0.12,0.07"];
edge [fontname="{FONT}", fontsize=14, arrowsize=0.7];
'''
    used = {k for k, v in LINK.items() if any(f'color="{v[0]}"' in b for b in body)}
    return head + legend(used) + "\n" + "\n".join(body) + "\n{rank=source; legend}\n}\n"


def local_tools():
    return [n("opn", "OPNsense", "firewall and router"), n("nas", "Synology NAS"), n("ha", "Home Assistant"),
            n("cups", "CUPS printers"), n("music", "Music Assistant"), n("ssh", "Local machines", "SSH")]


def local_links(src):
    return [e(src, t, "lan") for t in ("opn", "nas", "ha", "cups", "music", "ssh")]


def home():
    site = zone("site", "Remote site, optional", "site",
                n("sitegw", "Site gateway", "one upstream, works alone on its LAN"), n("sitetools", "Site tools"), dashed=True)
    vps = zone("vps", "Cloud VPS: just another remote", "cloud",
               n("boxmcp", "Box ops MCP server", "joined to the tailnet"), n("vpsapps", "Its own services"),
               n("vpscfd", "cloudflared", "its own tunnel, not the home one"))
    body = [*clients(), *edge_services(),
            zone("home", "Home network", "site",
                 zone("server", "Home server: Docker host", "host", stack("Canopy stack"),
                      n("oaitc", "tunnel-client", "OpenAI, dials out, optional", dashed=True),
                 n("oper", "Operator", "canopy CLI on the host"),
                      n("localmcp", "Local MCP servers", "your upstreams")),
                 zone("lan", "Local tools", "host", *local_tools())),
            vps, site, cloud_services(),
            *stack_links(), *client_links(), *outbound_links(),
            e("oper", "api", "ctl", "control API"), e("oper", "cli", "ctl", "canopy run"),
            e("gateway", "localmcp", "mcp", "registered upstreams"), *local_links("localmcp"),
            e("gateway", "boxmcp", "ts", "upstream over the tailnet"),
            e("cfedge", "vpscfd", "cf", "its own hostnames"), e("vpscfd", "vpsapps", "cf"), e("boxmcp", "vpsapps", "lan"),
            e("gateway", "sitegw", "ts", "one upstream"), e("sitegw", "sitetools", "lan")]
    return graph("Home Canopy", "The gateway lives on the home network. Runs in the source deployment, except where marked. "
                 "Canopy ships the stack only: local tools and VPS plugins are yours to add. 2026-11-05.", body)


def cloud():
    body = [*clients(with_home_device=True), *edge_services(),
            zone("vps", "Cloud VPS: Docker host", "cloud", stack("Canopy stack"),
                 n("oaitc", "tunnel-client", "OpenAI, dials out, optional", dashed=True),
                 n("oper", "Operator", "canopy CLI on the host"),
                 n("localmcp", "Cloud MCP servers", "your upstreams")),
            zone("otherbox", "Other cloud boxes", "cloud", n("boxmcp", "Box ops MCP server", "joined to the tailnet")),
            zone("home", "Home network", "site",
                 zone("homehost", "Small home machine", "host",
                      n("proxy", "Local proxy", "small gateway, one upstream,<br/>dials out, no open ports", "#aed581"),
                      n("proxymcp", "Local MCP servers")),
                 zone("lan", "Local tools", "host", *local_tools())),
            cloud_services(),
            *stack_links(), *client_links(), *outbound_links(),
            e("oper", "api", "ctl", "control API"), e("oper", "cli", "ctl", "canopy run"),
            e("athome", "mesh", "ts"),
            e("gateway", "localmcp", "mcp", "registered upstreams"),
            e("gateway", "boxmcp", "ts", "upstream over the tailnet"),
            e("gateway", "proxy", "ts", "one upstream, every local call: proxy dials out to the tailnet"),
            e("proxy", "proxymcp", "mcp"), *local_links("proxymcp"),
            e("athome", "proxy", "idea", "local tools, direct")]
    return graph("Cloud Canopy", "The gateway lives on a VPS, and a small proxy reaches home. A proposed template: not deployed as a whole. "
                 "The proxy is the site gateway pattern, which runs in the source deployment. 2026-11-05.", body)


for name, text in (("home-canopy", home()), ("cloud-canopy", cloud())):
    dot = HERE / f"{name}.dot"
    dot.write_text(text)
    subprocess.run(["dot", "-Tpdf", str(dot), "-o", str(HERE / f"{name}.pdf")], check=True)
    print(name)

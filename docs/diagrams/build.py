#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["pypdf"]
# ///
"""Build the Home Canopy and Cloud Canopy posters.

Run: uv run docs/diagrams/build.py. Needs Graphviz, Typst and pdftoppm. Fonts are in fonts/, so nothing is installed.
Per poster it writes <name>.dot, <name>.pdf (A1, for screens), <name>.png and <name>-print.pdf
(A1 with 3 mm bleed, crop marks, and TrimBox and BleedBox set) and <name>-print-a0.pdf (the same at A0). Layout is in poster.typ.
Keep the Canopy stack in stack() in step with compose.yaml.
"""
import json
import os
import subprocess
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject

HERE = Path(__file__).parent
FONT = "Atkinson Hyperlegible"
BUILD = HERE / ".build"
MM = 72 / 25.4
DATE = "2026-11-05"
TAGLINE = "Your AI universe under one roof"
CREDIT = "By Daniel Rosehill with Claude Opus 5.5"
LINK = {  # class: (colour, style, width, legend)
    "mcp":    ("#1b5e20", "solid", 1.8, "MCP over HTTP, with a token"),
    "ts":     ("#1565c0", "solid", 1.8, "Tailscale (WireGuard)"),
    "cf":     ("#e65100", "solid", 1.8, "Cloudflare: OAuth, Access, tunnel"),
    "oai":    ("#6a1b9a", "dashed", 1.5, "OpenAI tunnel"),
    "lan":    ("#7cb342", "bold", 2.6, "Local network: API or SSH"),
    "out":    ("#37474f", "solid", 1.2, "Outbound HTTPS"),
    "ctl":    ("#607d8b", "solid", 1.1, "Internal: control API, database"),
    "file":   ("#00838f", "solid", 1.6, "Files: S3 and presigned URLs"),
    "idea":   ("#c62828", "dashed", 1.8, "Not built: the anti-hairpin route"),
}
ZONE = {"internet": "#f5f5f5", "cf": "#fff3e0", "ts": "#e3f2fd", "site": "#e8f5e9", "host": "#f1f8e9",
        "stack": "#dcedc8", "cloud": "#e1f5fe", "oai": "#f3e5f5", "client": "#fafafa"}


KEY = {"gateway", "catalogue", "toolbox", "storage", "proxy", "mesh", "portal"}
TITLE_PT, KEY_PT, SUB_PT, EDGE_PT = 20, 26, 16, 16


def n(id, title, sub="", fill="white", dashed=False):
    sub = f'<BR/><FONT POINT-SIZE="{SUB_PT}" COLOR="#1c2b33">{sub}</FONT>' if sub else ""
    title = f'<FONT POINT-SIZE="{KEY_PT}">{title}</FONT>' if id in KEY else title
    style = "rounded,filled,dashed" if dashed else "rounded,filled"
    return f'{id} [label=<<B>{title}</B>{sub}>, style="{style}", fillcolor="{fill}"];'


def zone(id, label, fill, *body, dashed=False):
    style = "rounded,filled,dashed" if dashed else "rounded,filled"
    inner = "\n".join(body)
    return (f'subgraph cluster_{id} {{ label=<<B>{label}</B>>; labeljust=l; style="{style}"; '
            f'fillcolor="{ZONE[fill]}"; color="#78909c"; fontsize=23;\n{inner}\n}}')


def e(a, b, kind, label="", **kw):
    colour, style, width, _ = LINK[kind]
    label_colour = {"ctl": "#37474f", "lan": "#33691e"}.get(kind, colour)
    attrs = {"color": colour, "style": style, "penwidth": width, "fontcolor": label_colour, **kw}
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
    own = [n("cc", "Claude Code", "canopy-connector plugin:<br/>one connection, find, validate, /skill"),
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
    def sample(c, s):
        if s == "dashed":
            return f'<TD ALIGN="LEFT"><FONT COLOR="{c}"><B>- - - -</B></FONT></TD>'
        return f'<TD ALIGN="LEFT"><FONT COLOR="{c}"><B>———</B></FONT></TD>'

    rows = "".join(f'<TR>{sample(c, s)}<TD ALIGN="LEFT">{t}</TD></TR>' for k, (c, s, _, t) in LINK.items() if k in used)
    return ('legend [shape=plaintext, fontsize=18, label=<<TABLE BORDER="1" COLOR="#90a4ae" CELLBORDER="0" CELLSPACING="3" CELLPADDING="3">'
            f'<TR><TD COLSPAN="2" ALIGN="LEFT"><B>Key</B></TD></TR>{rows}'
            '<TR><TD ALIGN="LEFT">Dashed box</TD><TD ALIGN="LEFT">Opt-in compose profile, or optional</TD></TR></TABLE>>];')


def graph(nodesep, body):
    head = f'''digraph G {{
rankdir=LR; compound=true; newrank=true; nodesep={nodesep}; ranksep=0.6; splines=true; pad=0.1; bgcolor=transparent;
fontname="{FONT}";
node [shape=box, fontname="{FONT}", fontsize={TITLE_PT}, color="#37474f", margin="0.12,0.07"];
edge [fontname="{FONT}", fontsize={EDGE_PT}, arrowsize=0.8];
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
    return graph(0.25, body)


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
    return graph(0.55, body)


POSTERS = [
    {"name": "home-canopy", "number": "1", "title": "Home Canopy", "dot": home,
     "summary": "The gateway lives on the home network. Runs in the source deployment, except where marked. "
                "Canopy ships the stack only: local tools and VPS plugins are yours to add.",
     "short": "the gateway on a home server", "badge": "../brand/badge-home.png",
     "rings": [(1, ["cluster_own"]), (2, ["gateway", "toolbox", "catalogue"]), (3, ["cluster_lan"]),
               (4, ["cluster_cf"]), (4, ["cluster_tailnet"]), (5, ["storage"]), (6, ["cluster_vps"])],
     "notes": [("Thin clients", "one connector each, no credentials for your tools"),
               ("Progressive discovery", "agents search, then load only what they need"),
               ("Local stays local", "the gateway is on the same network as your tools"),
               ("Secure remote access", "Cloudflare for hosted clients, Tailscale for your devices"),
               ("File transport", "an S3 bucket with an expiry moves files to the server"),
               ("Offsite remotes", "a VPS is one more upstream, over the tailnet")]},
    {"name": "cloud-canopy", "number": "2", "title": "Cloud Canopy", "dot": cloud,
     "summary": "The gateway lives on a VPS, and a small proxy reaches home. A proposed template, not deployed as a whole. "
                "The proxy is the site gateway pattern, which runs in the source deployment.",
     "short": "the gateway on a VPS, with a local proxy at home", "badge": "../brand/badge-cloud.png",
     "rings": [(1, ["cluster_own"]), (2, ["gateway", "toolbox", "catalogue"]), (3, ["cluster_homehost"]),
               (4, ["cluster_cf"]), (4, ["cluster_tailnet"]), (5, ["storage"]), (6, ["athome"])],
     "notes": [("Thin clients", "one connector each, no credentials for your tools"),
               ("Progressive discovery", "agents search, then load only what they need"),
               ("Local proxy", "home reaches the cloud through one upstream, with no open ports"),
               ("Secure remote access", "Cloudflare for hosted clients, Tailscale for your devices"),
               ("File transport", "an S3 bucket with an expiry moves files to the server"),
               ("Anti-hairpin", "at home, local tools could skip the cloud: an idea, not built")]},
]


def highlights(poster, dot, env):
    """Find the rings in the laid-out graph, as fractions of the diagram, for poster.typ to draw."""
    # One run for both files, so the coordinates describe the PDF.
    pdf, coords = BUILD / f"{poster['name']}.pdf", BUILD / f"{poster['name']}.layout.json"
    subprocess.run(["dot", "-Tpdf", "-o", str(pdf), "-Tjson", "-o", str(coords), str(dot)], check=True, env=env)
    layout = json.loads(coords.read_text())
    # Measure the page, not the layout: Graphviz adds margins to a PDF.
    x0, y0, x1, y1 = map(float, layout["bb"].split(","))
    page = PdfReader(pdf).pages[0].mediabox
    width, height = float(page.width), float(page.height)
    pad = (width - (x1 - x0)) / 2
    pad_y = (height - (y1 - y0)) / 2
    boxes = {}
    for o in layout["objects"]:
        if "bb" in o:
            boxes[o["name"]] = tuple(map(float, o["bb"].split(",")))
        elif "pos" in o:
            x, y = map(float, o["pos"].split(","))
            w, h = float(o["width"]) * 36, float(o["height"]) * 36
            boxes[o["name"]] = (x - w, y - h, x + w, y + h)
    rings = []
    for number, names in poster["rings"]:
        b = [boxes[k] for k in names]
        left, right = min(v[0] for v in b), max(v[2] for v in b)
        low, high = min(v[1] for v in b), max(v[3] for v in b)
        rings.append({"n": number, "x": ((left + right) / 2 + pad) / width, "y": 1 - ((low + high) / 2 + pad_y) / height,
                      "w": (right - left) / width, "h": (high - low) / height})
    return {"aspect": width / height, "rings": rings, "notes": poster["notes"]}


def typst(poster, other, diagram, out, print_):
    inputs = {"title": poster["title"], "number": poster["number"], "summary": poster["summary"], "date": DATE,
              "other-number": other["number"], "other-title": other["title"], "other-summary": other["short"],
              "diagram": diagram, "print": "1" if print_ else "0", "badge": poster["badge"],
              "highlights": f".build/{poster['name']}.json", "tagline": TAGLINE, "credit": CREDIT}
    args = [x for k, v in inputs.items() for x in ("--input", f"{k}={v}")]
    subprocess.run(["typst", "compile", "--root", str(HERE.parent), "--font-path", str(HERE / "fonts"), *args,
                    str(HERE / "poster.typ"), str(out)], check=True)


def set_boxes(path, bleed=3, slug=9):
    """Tell the printer where the trim and bleed are. Typst does not set these."""
    writer = PdfWriter(clone_from=PdfReader(path))
    page = writer.pages[0]
    w, h = float(page.mediabox.width), float(page.mediabox.height)
    box = lambda m: RectangleObject([m * MM, m * MM, w - m * MM, h - m * MM])
    page.bleedbox, page.trimbox = box(slug), box(slug + bleed)
    writer.write(path)


def scale_to_a0(src, out):
    """A0 is A1 at root 2. Everything scales, so the bleed becomes 4.2 mm."""
    writer = PdfWriter(clone_from=PdfReader(src))
    writer.pages[0].scale_by(2 ** 0.5)
    writer.write(out)


def report(name):
    """Print the size the type comes out at, since that decides whether a poster reads on a wall."""
    data = json.loads((BUILD / f"{name}.json").read_text())
    page = PdfReader(BUILD / f"{name}.pdf").pages[0].mediabox
    box_w, box_h = (841 - 52 - 10) * MM, (594 - 158 - 9 - 22 - 6) * MM
    scale = min(box_w / float(page.width), box_h / float(page.height))
    line = lambda k: f"key {KEY_PT * scale * k:.1f}, box {TITLE_PT * scale * k:.1f}, label {EDGE_PT * scale * k:.1f}, small {SUB_PT * scale * k:.1f}"
    print(f"{name}: A1 {line(1)} | A0 {line(2 ** 0.5)}")


def main():
    BUILD.mkdir(exist_ok=True)
    conf = BUILD / "fonts.conf"
    conf.write_text(f"<fontconfig><dir>{HERE / 'fonts'}</dir><cachedir>{BUILD / 'fc'}</cachedir></fontconfig>")
    env = {**os.environ, "FONTCONFIG_FILE": str(conf)}
    for poster, other in zip(POSTERS, reversed(POSTERS)):
        name = poster["name"]
        dot = HERE / f"{name}.dot"
        dot.write_text(poster["dot"]())
        (BUILD / f"{name}.json").write_text(json.dumps(highlights(poster, dot, env)))
        diagram = f".build/{name}.pdf"
        typst(poster, other, diagram, HERE / f"{name}.pdf", False)
        typst(poster, other, diagram, HERE / f"{name}-print.pdf", True)
        set_boxes(HERE / f"{name}-print.pdf")
        scale_to_a0(HERE / f"{name}-print.pdf", HERE / f"{name}-print-a0.pdf")
        subprocess.run(["pdftoppm", "-png", "-scale-to", "3200", "-singlefile", str(HERE / f"{name}.pdf"), str(HERE / name)],
                       check=True)
        report(name)


main()

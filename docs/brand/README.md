# Brand assets

The mascot is a sloth in a green zip-up onesie, drawn from a photo of a plush toy. All images were generated
on [fal](https://fal.ai) on 2026-11-05.

| File | Use | Endpoint | Seed |
| --- | --- | --- | --- |
| `avatar.png` | Avatar, 512x512, transparent corners | `google/nano-banana-2.1/edit` | 11 |
| `banner.jpg` | README banner | `google/nano-banana-2.1/edit`, 21:9, 2K, with the avatar as reference | 21 |
| `social-preview.jpg` | GitHub social preview, 1280x640, a centre crop of the banner | As above | 21 |
| `poster-frame.jpg` | Background of the network posters, 5056x3392 | `google/nano-banana-2.1/edit`, 3:2, 4K, with the avatar as reference | 42 |
| `canopy-at-a-glance.png` | Overview diagram | `google/nano-banana-2.1/edit`, 16:9, 1K, with the avatar as reference | 31 |
| `home-canopy.png` | Home Canopy diagram | As above | 31 |
| `cloud-canopy.png` | Cloud Canopy diagram | As above | 31 |

The avatar's input was the photo with its background removed (`fal-ai/birefnet/v2`), flattened onto white.
Nano Banana kept the onesie and FLUX.3 Klein lost it, on the same prompt.

The three diagrams hold only short labels. The accurate, editable versions are the Mermaid diagrams in
[overview.md](../overview.md), so change those first.

## Prompts

Avatar:

> Keep this exact character. Turn it into a friendly flat illustrated mascot avatar, head and shoulders,
> centred, peeking out from lush rainforest canopy leaves on a solid deep green circular background. Clean
> bold shapes, soft shading, no text.

Banner:

> Keep this exact sloth character and its flat illustrated style. Wide GitHub banner: the sloth hangs happily from a thick branch high in a lush rainforest canopy, in the right half of the image. Beneath the leaves, small friendly glowing tool icons (a wrench, a terminal window, a plug, a puzzle piece, a light bulb) nestle among the foliage as if sheltering under one roof. Deep green and lime palette matching the character, soft morning light through the leaves. Left half, large clean rounded lettering: "Canopy". Directly below it, smaller: "All your skills and tools under one roof". Keep all important content within the central 80% of the width. No other text, logos or watermarks anywhere.

Overview diagram:

```text
A 16:9 architecture diagram for a technical README, flat illustrated style with a soft rainforest palette: cream background, deep green and lime cards, rounded corners, dark green text, thin dark arrows.
Title, top left, plain text, no box and no arrow: "One place for every agent".
Layout: three columns read left to right.
Left column, heading plain text with no box: "Sources". Two stacked cards: "Skills and plugins" and "MCP servers".
Centre column: one large rounded panel under a leafy tree canopy, with panel title "Canopy". Inside it two stacked cards: "Skills library" with small subtitle "progressive discovery", and "MCP aggregation" with small subtitle "MCPJungle, progressive".
Right column, heading plain text with no box: "Thin clients". Four stacked cards: "Claude Code", "claude.ai", "ChatGPT", "Your own agents".
Arrows: "Skills and plugins" points right to "Skills library". "MCP servers" points right to "MCP aggregation". Exactly four arrows leave the right edge of the outer panel from one shared trunk line, one to each client card, pointing right. No arrow starts from an inner card.
Each card contains only its label and any subtitle given: no sample content, mini-diagrams, screenshots or example text inside it.
No other text, logos or watermarks anywhere.
Include this exact sloth character small, in the bottom right corner, not covering any label or arrow.
```

Home Canopy diagram:

```text
A 16:9 architecture diagram for a technical README, flat illustrated style with a soft rainforest palette: cream background, deep green and lime cards, rounded corners, dark green text, thin dark arrows.
Title, top left, plain text, no box and no arrow: "Home Canopy".
Layout: three columns read left to right.
Left column, one card: "Clients" with small subtitle "one connector each".
Centre: one large panel shaped like a house under a leafy tree canopy, with panel title "Home network". Inside it two stacked cards: "Canopy" on top, and "Local tools" below with small subtitle "Home Assistant, OPNsense, NAS, printers".
Right column, two stacked cards: "Cloud APIs" and "Cloud VPS".
Arrows: "Clients" to "Canopy", edge label "Tailscale or Cloudflare". "Canopy" down to "Local tools", a short thick green arrow, edge label "local network". "Canopy" right to "Cloud APIs", edge label "outbound". "Canopy" right to "Cloud VPS", edge label "Tailscale". Exactly four arrows.
Each card contains only its label and any subtitle given: no sample content, mini-diagrams, screenshots or example text inside it.
No other text, logos or watermarks anywhere.
Include this exact sloth character small, in the bottom right corner, not covering any label or arrow.
```

Cloud Canopy diagram:

```text
A 16:9 architecture diagram for a technical README, flat illustrated style with a soft rainforest palette: cream background, deep green and lime cards, rounded corners, dark green text, thin dark arrows.
Title, top left, plain text, no box and no arrow: "Cloud Canopy".
Layout: an upper row and a lower row.
Upper row: on the left a card "Clients" with small subtitle "one connector each". In the centre a large panel shaped like a soft cloud, with one card inside: "Canopy" with small subtitle "on a VPS". On the right a card "Cloud APIs".
Lower row: under the centre and right, one panel shaped like a house under a leafy tree canopy, with panel title "Home network". Inside it two side-by-side cards: "Local proxy" and "Local tools".
Arrows: "Clients" to "Canopy". "Canopy" to "Cloud APIs". "Canopy" down to "Local proxy", edge label "tunnel, dials out from home". "Local proxy" to "Local tools". A dashed green arrow from "Clients" down and right to "Local proxy", edge label "at home: no hairpin". Exactly five arrows.
Each card contains only its label and any subtitle given: no sample content, mini-diagrams, screenshots or example text inside it.
No other text, logos or watermarks anywhere.
Include this exact sloth character small, in the bottom right corner, not covering any label or arrow.
```

Poster background:

```text
Keep this exact sloth character and its flat illustrated style. Decorative poster background with no text. A dense band of lush rainforest canopy foliage runs across only the top 19% of the image, ending in a clean straight edge, deep greens with lime highlights, very dark behind the left 55% so light lettering can sit there. The sloth sits small and completely inside that band on a thick branch towards the right, with no part of it below the band. A narrow fringe of monstera and fern leaves runs along the left and right edges, no deeper than 3% of the image, and small clusters in the two bottom corners only. Everything else is empty, plain flat warm cream. No text, letters, logos, icons or watermarks anywhere.
```

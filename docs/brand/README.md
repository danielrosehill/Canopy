# Brand assets

The mascot is a sloth in a green zip-up onesie, drawn from a photo of a plush toy. All images were generated
on [fal](https://fal.ai) on 2026-11-05.

| File | Use | Endpoint | Seed |
| --- | --- | --- | --- |
| `avatar.png` | Avatar, 512x512, transparent corners | `google/nano-banana-2.1/edit` | 11 |
| `banner.jpg` | README banner | `google/nano-banana-2.1/edit`, 21:9, 2K, with the avatar as reference | 21 |
| `social-preview.jpg` | GitHub social preview, 1280x640, a centre crop of the banner | As above | 21 |
| `canopy-at-a-glance.png` | Overview diagram | `openai/gpt-image-2.5/flare/text-to-image`, `quality: medium` | None |
| `why-at-home.png` | Cloud against home diagram | As above | None |

The avatar's input was the photo with its background removed (`fal-ai/birefnet/v2`), flattened onto white.
Nano Banana kept the onesie and FLUX.3 Klein lost it, on the same prompt.

The two diagrams hold only short labels. The accurate, editable versions are the Mermaid diagrams in
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
Layout: three columns read left to right.
Left column, heading plain text with no box: "Sources". Two stacked cards: "Skills and plugins" and "MCP servers".
Centre column: one large rounded panel shaped like a house under a leafy tree canopy, with panel title "Canopy on your home server". Inside it two stacked cards: "Skills library" with small subtitle "progressive discovery", and "MCP aggregation" with small subtitle "MCPJungle, progressive".
Right column, heading plain text with no box: "Clients". Four stacked cards: "Claude Code", "claude.ai", "ChatGPT", "Your own agents".
Arrows: "Skills and plugins" points right to "Skills library". "MCP servers" points right to "MCP aggregation". The centre panel has one arrow to each of the four client cards, pointing right.
Title, top left, plain text, no box and no arrow: "One place for every agent".
Each card contains only its label and subtitle: no sample content, mini-diagrams, screenshots or example text inside it.
No other text, logos or watermarks anywhere.
```

Cloud against home diagram:

```text
A 16:9 comparison diagram for a technical README, flat illustrated style with a soft rainforest palette: cream background, deep green and lime cards, rounded corners, dark green text, thin dark arrows. Two side-by-side panels.
Title, top left, plain text, no box and no arrow: "Why it lives at home".
Left panel, heading plain text: "Gateway in the cloud". Three cards in a row left to right: "Agent", "Cloud gateway", "Light bulb". Arrow from "Agent" to "Cloud gateway". A long, drooping, awkward dashed red arrow from "Cloud gateway" down to "Light bulb", labelled "tunnel back into the home".
Right panel, heading plain text: "Gateway at home". Three cards: "Agent", "Canopy", "Light bulb". Arrow from "Agent" to "Canopy". A short solid green arrow from "Canopy" to "Light bulb", labelled "local network". A second card above, "Cloud services", with an arrow from "Canopy" up to it, labelled "still reachable".
Each card contains only its label: no sample content, mini-diagrams, screenshots or example text inside it.
No other text, logos or watermarks anywhere.
```

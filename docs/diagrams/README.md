# Network diagrams

Two posters, a pair: Implementation 1 (Home Canopy) and Implementation 2 (Cloud Canopy). Each names the other as
its alternative.

| File | For |
| --- | --- |
| `<name>.pdf` | Screens. A1 at trim size |
| `<name>.png` | The README, 3200 px wide |
| `<name>-print.pdf` | Printing. A1, 3 mm bleed, crop marks |
| `<name>.dot` | Generated Graphviz source of the diagram |

## Printing

- **Size:** A1 landscape, 841 x 594 mm. It fits a 610 mm roll. Type in the diagram prints at about 9 to 11 pt.
- **Larger:** the diagram and type are vector, so A0 is a straight 141% scale. The background art is 150 dpi at
  A1 and about 106 dpi at A0, which suits viewing from a distance.
- **Smaller:** avoid going below A2. At A3 the smallest type is under 5 pt.
- **Print file:** `TrimBox` and `BleedBox` are set, fonts are embedded and the bleed is 3 mm. Colour is RGB, so
  ask the shop to convert it, or to print RGB directly, which most large-format printers do.
- **Paper:** matte or satin poster paper. Print at 100%, not "fit to page", or the crop marks will shift.

## Highlights

The numbered capsules and the "What to look at" list come from `rings` and `notes` in `POSTERS` in `build.py`. A ring
names nodes or clusters, and its position comes from Graphviz, so it follows the layout. The coordinates and the PDF
must come from one `dot` run, and the offset is measured from the PDF page, because Graphviz adds a margin there.
Do not set `margin=0` on the graph to avoid that: clusters inherit it, and the zones then overlap.

## Licence

The posters are CC BY 4.0, by Daniel Rosehill with Claude Opus 5.5. The code that builds them is MIT, like the rest
of the repo.

## Rebuilding

```bash
uv run docs/diagrams/build.py
```

Needs Graphviz, Typst and `pdftoppm`. Fonts are in `fonts/`, under the SIL Open Font License, and are used without
installing them. `build.py` holds the components and connections, and `poster.typ` holds the layout.

The Canopy stack in `build.py` was checked against `compose.yaml` and the source on 2026-11-05. Change one
when the other changes. Update `DATE` in `build.py` when you do.

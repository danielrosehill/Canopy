# Network diagrams

Two posters, a pair: Implementation 1 (Home Canopy) and Implementation 2 (Cloud Canopy). Each names the other as
its alternative.

| File | For |
| --- | --- |
| `<name>.pdf` | Screens. A1 at trim size |
| `<name>.jpg` | The README preview, 3200 px wide |
| `<name>-print.pdf` | Printing. A1, 3 mm bleed, crop marks |
| `<name>-print-a0.pdf` | Printing. The same poster at A0 |
| `<name>.dot` | Generated Graphviz source of the diagram |

## Printing

Both sizes are ready to print. Choose by how far away people will stand.

| Printed type, in points | Home, A1 | Cloud, A1 | Home, A0 | Cloud, A0 |
| --- | --- | --- | --- | --- |
| Key components, such as the gateway | 18 | 16 | 26 | 23 |
| Other box names | 14 | 12 | 20 | 18 |
| Connection labels and small print | 11 | 10 | 16 | 14 |
| "What to look at" list | 22 | 22 | 31 | 31 |

`build.py` prints the diagram rows on every build.

- **A0, 1189 x 841 mm:** for a wall. Estimated comfortable reading distance is about 1 m for small print, and 1.5 m
  for key components. It fits a 914 mm roll. Use `<name>-print-a0.pdf`.
- **A1, 841 x 594 mm:** for reading up close, at about 0.7 m for small print. It fits a 610 mm roll. Use
  `<name>-print.pdf`.
- **Distances are estimates,** from a rule of thumb of 4 mm of capital height per metre, not a test print. To check
  before paying for a poster, print part of the PDF at 100% on a sheet of A4, tape it up and step back.
- **Background art:** 150 dpi at A1 and about 106 dpi at A0, which suits viewing from a distance. Diagram and type are
  vector, so stay sharp at any size.
- **Print files:** `TrimBox` and `BleedBox` are set and fonts are embedded. Bleed is 3 mm at A1 and 4.2 mm at A0.
  Colour is RGB, so ask the shop to convert it, or to print RGB directly, which most large-format printers do.
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

Needs Graphviz, Typst and `pdftoppm`. Fonts are in `fonts/` (Atkinson Hyperlegible for reading, Fredoka for titles), under the SIL Open Font License, and are used without
installing them. `build.py` holds the components and connections, and `poster.typ` holds the layout.

The Canopy stack in `build.py` was checked against `compose.yaml` and the source on 2026-11-05. Change one
when the other changes. Update `DATE` in `build.py` when you do.

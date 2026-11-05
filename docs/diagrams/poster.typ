// One poster. Built by build.py, which passes these inputs:
//   title, number, summary, other-number, other-title, other-summary, diagram, print ("1" adds bleed and marks)
#let i = sys.inputs
#let print = i.at("print", default: "0") == "1"
#let trim-w = 841mm
#let trim-h = 594mm
#let bleed = if print { 3mm } else { 0mm }
#let slug = if print { 9mm } else { 0mm }
#let off = bleed + slug
#let deep = rgb("#123524")
#let lime = rgb("#b5d96a")
#let cream = rgb("#fbf3e0")

#set page(width: trim-w + 2 * off, height: trim-h + 2 * off, margin: 0mm)
#set text(font: "Nunito", fill: deep)

// Art runs to the bleed edge.
#place(top + left, dx: slug, dy: slug,
  box(width: trim-w + 2 * bleed, height: trim-h + 2 * bleed, clip: true,
    image("../brand/poster-frame.jpg", width: 100%, height: 100%, fit: "cover")))

// Header: all of it stays well inside the trim.
#place(top + left, dx: off + 30mm, dy: off + 18mm,
  block(fill: deep.transparentize(12%), radius: 9mm, inset: (x: 10mm, y: 8mm),
    grid(columns: (auto, auto), column-gutter: 9mm, align: horizon,
      image("../brand/avatar.png", width: 80mm),
      stack(spacing: 5mm,
        text(size: 30pt, weight: 800, fill: lime, tracking: 1.5pt)[IMPLEMENTATION #i.number OF 2],
        text(font: "Fredoka", size: 118pt, weight: 600, fill: cream, i.title),
        v(9mm),
        block(width: 300mm, text(size: 26pt, fill: cream, i.summary)),
      ))))

// Diagram panel.
#let panel-x = 26mm
#let panel-y = 158mm
#let foot = 22mm
#let panel-w = trim-w - 2 * panel-x
#let panel-h = trim-h - panel-y - 9mm
#place(top + left, dx: off + panel-x, dy: off + panel-y,
  block(width: panel-w, height: panel-h, fill: rgb("#fffdf7"), radius: 7mm, stroke: 0.6mm + deep.transparentize(70%)))
#place(top + left, dx: off + panel-x + 5mm, dy: off + panel-y + 4mm,
  box(width: panel-w - 10mm, height: panel-h - foot - 6mm,
    align(center + horizon, image(i.diagram, width: 100%, height: 100%, fit: "contain"))))

// Footer: the URL, and the way to the other poster.
#place(top + left, dx: off + panel-x + 8mm, dy: off + panel-y + panel-h - foot,
  box(width: panel-w - 16mm, height: foot - 3mm,
    grid(columns: (1fr, auto), align: horizon,
      stack(spacing: 2.5mm,
        text(font: "Fredoka", size: 30pt, weight: 600)[github.com/danielrosehill/Canopy],
        text(size: 17pt)[#i.date. Self-hosted progressive discovery for AI agents.]),
      block(fill: deep, radius: 5mm, inset: (x: 8mm, y: 3.5mm),
        stack(spacing: 2mm,
          text(size: 15pt, weight: 800, fill: lime, tracking: 1pt)[ALTERNATIVE: IMPLEMENTATION #i.at("other-number")],
          text(size: 21pt, fill: cream)[#text(font: "Fredoka", weight: 600, i.at("other-title")): #i.at("other-summary")])))))

// Crop marks sit in the slug, outside the bleed.
#if print {
  let mark(x, y, dx, dy) = place(top + left, dx: x, dy: y, line(start: (0mm, 0mm), end: (dx, dy), stroke: 0.25pt + black))
  let len = 6mm
  let gap = bleed + 1mm
  for x in (off, off + trim-w) {
    mark(x, off - gap - len, 0mm, len)
    mark(x, off + trim-h + gap, 0mm, len)
  }
  for y in (off, off + trim-h) {
    mark(off - gap - len, y, len, 0mm)
    mark(off + trim-w + gap, y, len, 0mm)
  }
}

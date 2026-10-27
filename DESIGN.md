---
name: Keen (Key Evidence ENgine)
description: Evidence-first ISMS workspace in soft tinted glass, themed by a single accent palette
colors:
  accent: "#004987"
  accent-deep: "#003768"
  accent-wash: "#e4f2ff"
  accent-rgb: "0,73,135"
  accent-contrast: "#fff"
  link-hover: "#002c57"
  canvas: "#f1f6fc"
  ink: "#1f1f2a"
  muted: "#5e5e75"
  chart-tint: "var(--keen-accent-rgb)"
  dark-ink: "#edf0f6"
  dark-canvas: "#141820"
  dark-panel: "#202631"
  dark-panel-2: "#29313e"
  dark-border: "#465061"
typography:
  body:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial"
    fontSize: "1rem"
    fontWeight: 400
  kpi-value:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial"
    fontSize: "22px"
    fontWeight: 700
    letterSpacing: "-0.2px"
  label:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial"
    fontSize: "12px"
    fontWeight: 400
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace"
    fontSize: "12.5px"
rounded:
  pill: "999px"
  card: "18px"
  media: "14px"
  nav-link: "10px"
  toggle: "8px"
spacing:
  container-tight: "max-width 1400px"
  kpi-padding: "14px 16px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-contrast}"
    rounded: "0.375rem"
    size: "Bootstrap .btn (0.375rem 0.75rem)"
  button-primary-hover:
    backgroundColor: "{colors.accent-deep}"
    textColor: "{colors.accent-contrast}"
  button-outline-primary:
    textColor: "{colors.accent-deep}"
    backgroundColor: "transparent"
    rounded: "0.375rem"
  card:
    backgroundColor: "#fff"
    textColor: "{colors.ink}"
    rounded: "{rounded.card}"
  card-header:
    backgroundColor: "linear gradient rgba(0,73,135,0.10) → rgba(0,73,135,0.02)"
    textColor: "{colors.ink}"
  kpi:
    backgroundColor: "rgba(255,255,255,0.78)"
    textColor: "{colors.ink}"
    rounded: "{rounded.card}"
    padding: "{spacing.kpi-padding}"
  badge-soft:
    backgroundColor: "rgba(0,73,135,0.10)"
    textColor: "{colors.accent-deep}"
    rounded: "Bootstrap .badge"
  badge-chip:
    backgroundColor: "rgba(31,31,42,0.04)"
    textColor: "{colors.ink}"
  tag-pill:
    backgroundColor: "rgba(0,73,135,0.14)"
    textColor: "{colors.accent-deep}"
    rounded: "{rounded.pill}"
    padding: "3px 10px"
---

# Design System: Keen (Key Evidence ENgine)

## Overview

**Creative North Star: "The Tinted Glass Console"**

Keen's interface is a calm, airy, quietly confident stack of soft panels in a single accent colour. The page canvas is a flat tinted wash (`#f1f6fc` under the default palette); on it sit glassy, gently lifted cards with large rounded corners (18px) and accent-tinted translucent borders. One accent — **Archival Ink** `#004987` in the shipped default — is the system's single voice: it tints card headers, badges, table rules, graph links, and the fixed navbar alike, always as a wash or rgba tint, never as heavy decoration. Depth comes from translucency (white surfaces at 0.70–0.95 opacity) and diffuse low shadows, not from crisp layer boundaries.

The system is deliberately themable: the accent palette is swapped wholesale by `data-theme` on `<html>` (ink default; ocean, forest, sunset, rose, slate, teal), each with a `-dark` variant persisted as one account preference. Components never hard-code accent hex values — they reference `--keen-accent*` variables (with Bootstrap's `--bs-primary` bound to the same variables), so a theme change re-colours the entire console without layout changes.

Typography is the platform's own voice — a system sans stack and Bootstrap's hierarchy — with numbers, identifiers and evidence text set in a compact monospace face. Density is mild: generous card padding but compact table type (12px helper labels, 12.5px mono).

**Provenance.** Most of this system lives in the adjacent **mosp-design-system** repository (`src/css/00-…css` through `90-…css`, bundled by `scripts/build.mjs` into `dist/styles.css`, vendored into Keen as `/vendor/mosp-design-system/styles.css` and imported from `services/ui/public/styles.css`; shared behaviour such as the navbar, sorting, toasts and date formatting ships in `dist/app.js`). Keen's own `styles.css` adds page-specific refinements on top — it never redefines shared tokens. Any new visuals that are generic across FERCBusters apps belong in mosp-design-system; anything Keen-specific belongs in Keen's `styles.css`.

**Key Characteristics:**
- One accent voice, applied as rgba washes and tints over near-white translucent surfaces.
- Soft, large radii: 18px cards/KPI/tabs rail, 14px media, 10–12px controls, 999px pills and chips.
- Depth by translucency + diffuse ambient shadow (`0 10px 30px rgba(20,20,40,0.06)`).
- System sans typography; mono for identifiers, JSON and event payloads.
- Full themability through `--keen-accent*` variables; seven palettes with dark variants.
- Bootstrap 5 base (components, grid, utility classes) with tables as the primary data surface.

## Colors

The palette is one accent hue plus dark ink neutrals on a tinted off-white canvas; every other role is derived by alpha-tinting the accent or the ink.

### Primary
- **Archival Ink** (`#004987`, accent; rgb `0,73,135`): the system's single colour voice. Navbar background, primary buttons, card-header gradients, badge and chip washes, table header rules, graph links and brushes, focus rings. Used as a solid only on the navbar and primary buttons — everywhere else at 0.06–0.24 alpha over white.
- **Ink Wash** (`#e4f2ff`, accent-100): selected-tab and active-state tint (e.g. light tabs background).

### Neutral
- **Page Canvas** (`#f1f6fc`, `--bs-body-bg`): flat, theme-bound tinted body background.
- **Ink** (`#1f1f2a`, `--keen-ink`): body text, chart-agnostic darks, chip text.
- **Ink Muted** (`#5e5e75`, `--keen-muted`): helper labels, KPI labels, small-muted text (12px).
- **Deep Accent (hover/links)** (`#003768`, accent-700) and **Link Hover** (`#002c57`): links and pressed/hover states.
- **White surfaces**: cards, KPI blocks and question threads at 0.70–0.92 opacity for the glass feel.

### Other palette themes
Each `data-theme` replaces the accent family and canvas tint in one move (ocean `#2563eb`, forest `#16a34a`, sunset `#f97316`, rose `#db2777`, slate `#475569`, teal `#0f766e`, purple `#7e57c2`, each with matching `-700`/`-100`/`-rgb`/hover/canvas values and white-on-accent contrast, except sunset's contrast, which is `#111827`).

### Dark appearance
`data-bs-theme="dark"` retains the accent hue but replaces light surfaces: canvas `#141820`, panels `#202631`, tertiary `#29313e`, border `#465061`, ink `#edf0f6`, muted `#b7bfce`; accent-100 becomes a `color-mix` of accent and panel. Cards, modals, dropdowns, tables, inputs and KPIs are remapped centrally in `90-taxonomy-and-utilities.css`.

### Named Rules
**The One Voice Rule.** A single accent owns the identity. Never introduce a second decorative hue; role semantics outside the accent (risk score pills, Bootstrap success/warning/danger states) are the only sanctioned exceptions.
**The Wash-Not-Solid Rule.** Accent colour is used solid only on the navbar and primary buttons; all other accent application is an rgba tint over white or the wash.

## Typography

**Body Font:** system sans stack (`ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial`) — no webfont is loaded; Bootstrap 5 supplies the scale.
**Label/Mono Font:** compact mono stack (`ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace`) at 12.5px for the `.mono` utility — IDs, tokens, JSON, event payloads.

**Character:** Platform-native and unassertive; hierarchy, not personality, does the work.

### Hierarchy
- **Emphasis (KPI value)** (700, 22px, -0.2px): metric numbers on KPI blocks.
- **Title** (600–700, 1rem+): page headings via Bootstrap headings and card titles; sticky table headers at 700 in slightly reduced ink.
- **Body** (400, 1rem): default; table cells and wrap-dense text run vertically top-aligned.
- **Label** (400, 12px, `--keen-muted`): helper and KPI labels (`.kpi .label`, `.small-muted`).
- **Nav** (Bootstrap defaults, 0.8–0.95rem, white on accent): navbar brand and links.
- **Data labels (SVG)** (600–700, 10–16px): D3 node/axis/legend labels via `.viz-axis` and `.viz-legend` classes.

### Named Rules
**The System-Stack Rule.** No webfonts. Faces come from the platform; express hierarchy through weight and size, not through type purchases.

## Layout

Bootstrap 5 grid and utilities on mostly-static HTML pages (no SPA framework; each page is its own HTML with a module script). Standard container with `.container-tight` (max-width 1400px) for dense working surfaces. The fixed-top navbar (`.navbar-keen`) has a JS-measured height (`--ui-nav-height`) used by body padding and sticky table headers; pages pin content beneath it. Sticky affordances are a first-class pattern: sticky `<thead>` under the navbar, and `.keen-sticky-topblock` (blurred 8px backdrop, bottom hairline) for pinned filter rows, with `body.keen-sticky-disabled` opting a page out. Responsive behaviour is Bootstrap-breakpoint driven: below 1400px the navbar groups stack into the collapsed bar, below 992px tables switch from sticky headers to horizontal scroll wrappers. Spacing follows Bootstrap rhythm; KPI blocks pad `14px 16px`.

## Elevation & Depth

Gently lifted, ambient. Depth is the glass model: translucent white surfaces (0.70–0.95) over the tinted canvas, defined by accent-tinted translucent borders, with diffuse, low-opacity shadows (near-black cool-neutral anchors, opacity 0.04–0.22) providing soft presence rather than structure. Nothing flies high; the one intentional exception is the fixed accent navbar with its visible glow.

### Shadow Vocabulary
- **Surface presence** (`0 10px 30px rgba(20, 20, 40, 0.06)`): resting cards.
- **Primary action / KPI** (`0 10px 22px rgba(20, 20, 40, 0.05)`): KPI blocks.
- **Navbar glow** (`0 6px 18px rgba(var(--keen-accent-rgb), 0.22)`): fixed navbar, accent-tinted.
- **Media lift** (`0 8px 18px rgba(20, 20, 40, 0.06)`): evidence images and PDF cards.
- **Toolbar float** (`0 6px 18px rgba(15, 23, 42, 0.12)`): graph zoom control pill.
- **Hover rise** (`translateY(-2px)` + `0 0.65rem 1.5rem rgba(15, 23, 42, 0.1)`): home explorer entry/node cards.

### Named Rules
**The Ambient-Only Rule.** Shadows signal that a surface exists above the canvas, not that it is more important. Strong accent shadows are reserved for the navbar; everything else barely lifts.

## Shapes

Soft and generous. Cards and KPI blocks at 18px radius; media frames, graph cards and question posts at 12–14px; admin rails and their nav links at 14–18px; nav links at 10px; small toggles at 8–10px. Pills and chips (tags, tab buttons, score pills, zoom buttons) go all the way to 999px. Distinctive geometry: translucency as material (surface = white at partial alpha), accent borders always as 1px alpha-tinted lines (0.16–0.55), dashed accent-tinted guides for tree child-edges, and circular dot markers inside tag pills. Borders carry the definition; backgrounds rarely change for state — they deepen by a small alpha step on hover/selection (0.06 → 0.08 → 0.16 → 0.22 → 0.4/0.65).

## Components

Rounded soft-blocks on Bootstrap 5 primitives. All shared component styling is owned by mosp-design-system and themed via `--keen-accent*`.

### Buttons
- **Shape:** Bootstrap 5 default radius (0.375rem), tinted with the theme.
- **Primary:** solid accent `--keen-accent` with `--keen-accent-contrast` text; hover/active drop to `--keen-accent-700`. Focus ring uses the accent rgb.
- **Outline primary:** accent-700 text, 55% alpha accent border; hover fills solid accent. Disabled keeps the outline at lower alpha with no fill.
- **On-accent (navbar):** white ghost controls — 0.16 alpha white hover pill, light borders on inputs/buttons.
- **Hover/Focus:** standard Bootstrap transitions; no custom motion language beyond a 120ms ease used by pills and hover-rise cards.

### Chips / Badges
- **badge-soft:** accent wash background (0.10), accent-deep text, 1px 0.24 alpha accent border, weight 600.
- **badge-chip:** near-invisible ink wash (0.04) and hairline ink border.
- **badge-source:** deterministic per-source tint via `--src-rgb` — tinted background, text in the raw rgb of the source hue; not theme-bound, since sources keep their identity across themes.
- **tag:** 999px pill, accent wash 0.14 with accent-deep text, optional `.dot` colour marker; semantic taxonomy pills can be recoloured per-capability via `--cap-color` with `color-mix` relationships (selected state deepens to 0.22 background).
- **score-pill:** semantic risk colours (green `#198754`, yellow `#ffc107` with dark text, red `#dc3545`) — the sanctioned exception to the One Voice Rule.

### Cards / Containers
- **Corner Style:** 18px, matched by Bootstrap card-part variables.
- **Background:** white (or `--bs-secondary-bg` in dark) over the tinted canvas.
- **Shadow Strategy:** ambient-only surface shadow.
- **Border:** 1px accent at 0.20 alpha.
- **Header:** vertical accent gradient (0.10 → 0.02) over a 0.18 alpha accent bottom rule.
- **KPI:** translucent white (0.78), 18px radius, label 12px muted + value 22px/700.
- **Containers of controls** (admin rails, question threads, taxonomy browser): same family, 12–16px radius, translucent white backs.

### Inputs / Fields
- **Style:** light glass — white at ~0.92–0.95 alpha over the canvas (0.35 alpha white border when on the navbar).
- **Focus:** Bootstrap focus ring with the theme's accent rgb shadow.
- **Multi-select:** wrapped checkbox list inside a 12px glass panel (`.keen-multiselect-wrap`), options with 10px hover pills.

### Navigation
Fixed-top accent navbar (`.navbar-keen`): white text, 10px-radius hover/active pills at 0.16/0.18 white alpha, brand icon (28px, white rounded 9px background), framework selector, search box, notification bell; three density states (compact, tight, overflow) progressively compress type down to 0.74rem before ellipsising the brand. In-page secondary nav uses the admin tab rail (18px glass back, 14px items, 0.16 alpha active accent wash with accent-700 text) or the lightweight pill tabs (999px, accent-100 active). Anchor-underline links come in subtle ink (`.link-subtle`, 0.75 alpha ink, underlines on hover).

### Tables
The primary data surface. Sticky 700-weight headers pinned below the navbar with an accent-tinted inset rule and body-bg backing; `border-collapse: separate`; `.table-responsive` un-clip-scrolls on desktop so stickiness follows the page, switching back to horizontal scroll and static headers below 992px. Sortable headers earn ↑/↓ glyph pseudo-elements. Row loading uses a veiled `rgba(248,250,252,0.65)` strip.

### Evidence Media (signature)
Evidence thumbnails crop to 14px-rounded frames with the media-lift shadow (`object-fit: cover`, max-height 220px); PDF thumbnails switch to `contain` on a 0.02 black wash so documents are never cropped; print/export strips shadows and prefers full readable images. Evidence PDFs are export-grade first-class content — print headers/footers, page numbers and link suppression are part of the system.

### Visualisations (signature)
D3 chart containers share the 18px radius + 0.22 accent border of cards; node tiles follow a two-ink system — accent-tinted fills for source nodes, ink-dark fills for control nodes — with white 11px/600 stroke-haloed labels on dark tiles and dark labels with a background-coloured halo on light canvases. Graph links are 28–30% alpha accent strokes; brushes and zoom controls use the accent family; every swatch exposed for tinting reads from the theme variables.

## Do's and Don'ts

### Do:
- **Do** theme everything through `--keen-accent*` variables (and `--bs-primary`); never hard-code an accent hex in app CSS.
- **Do** apply the accent as an alpha wash (0.06–0.24) for surfaces, borders and tints; reserve solid accent for the navbar and primary buttons.
- **Do** use the established radius scale: 999px pills, 18px cards, 14px media, 10–12px small controls.
- **Do** keep tables sticky-headered on desktop with page-driven scrolling and the body-bg header backing.
- **Do** use `.mono` for identifiers, tokens and payload text.
- **Do** state dark-variant concerns alongside any new shared component theming in mosp-design-system.

### Don't:
- **Don't** introduce a second decorative accent; semantic status colours (risk pills, Bootstrap states) are the only exceptions.
- **Don't** add webfonts or replace the system type stacks.
- **Don't** use structural or coloured shadows; shadows stay in the ambient vocabulary in Elevation & Depth.
- **Don't** put Keen-generic components into `services/ui/public/styles.css`; shared things belong in mosp-design-system, Keen-specific overrides stay scoped to Keen's selectors.
- **Don't** clone per-source or per-capability colours into fixed CSS; they are per-entity tints applied via CSS variables (`--src-rgb`, `--cap-color`).
- **Don't** rely on backbone JS rebuilds of Bootstrap components; pages are static HTML + Bootstrap + the MOSP `app.js` helpers.

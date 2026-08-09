# Design — Revenue Recovery Voice Agent

This is the locked visual system for the prototype dashboard. Route behavior,
API contracts, and existing information architecture remain unchanged.

## Genre

Modern-minimal: quiet, technical, and easy to scan during a live call review.

## App structure

- App pages: Workbench with a stat strip, review surface, and supporting panels.
- Navigation: content-sized floating-pill treatment inside the page shell.
- Footer: inline rule with a short operational note.
- Enrichment: none. The product state and call data are the visual material.

## Brand palette

All UI color values are defined in `apps/web/app/tokens.css` and use the brand
palette supplied for this prototype:

| Token | Value | Role |
| --- | --- | --- |
| `--brand-silver` | `#d9dada` | raised neutral surface |
| `--brand-steel` | `#adaeb0` | secondary text and quiet rules |
| `--brand-blue` | `#c3d4e8` | active state and signal accent |
| `--brand-gray` | `#c6c7c8` | borders and dividers |
| `--brand-soft` | `#f3f3f3` | application canvas |
| `--brand-slate` | `#3e4246` | supporting ink |
| `--brand-ink` | `#121314` | primary ink and dark panels |
| `--brand-white` | `#f7f7f7` | surface and light text |

## Typography

- Display: `Trebuchet MS`, bold, tight tracking.
- Body: `Segoe UI`, `Arial`, sans-serif.
- Data: `Consolas`, `ui-monospace`, monospace.
- Headings are roman, never italic. Data labels use uppercase mono sparingly.

## Spacing and shape

Use the 4-point spacing tokens from `tokens.css`. Surfaces use a visible
1-pixel rule, restrained rounded corners, and no gradients or glass effects.
Buttons and navigation affordances stay single-line at every supported width.

## Motion and states

Motion is limited to hover/focus transitions and the live waveform. Focus is
immediate and visible. Reduced-motion mode disables the waveform animation and
keeps all state changes readable without movement.

## Page allowances

Calls, call detail, Live, Agent, and Analytics share the same shell, type,
palette, status language, and panel rhythm. Empty and error states remain
explicit; the dashboard must not invent call or revenue metrics.

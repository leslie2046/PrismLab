# Visual system

Use Dify-inspired workspace surfaces: a soft neutral sidebar framing a clean main plane. Light: background #f2f4f7, surface white, text #171a21, border #e4e7ec. Dark: background #191a1c, surface #202123, subtle #28292d, border #343538, readable off-white text. Use semantic CSS variables in index.css, never page-local hard-coded colors. Primary blue #155eef with white text in either theme; selected navigation uses a neutral raised surface. Keep semantic success, warning and destructive palettes distinct.

Use 8 px spacing increments with 4 px adjustments. Main content padding 24–32 px desktop, 12–16 px mobile. Sidebar 224 px. Controls 34–36 px, compact utilities 32 px, touch targets at least 44 px where needed. Corners 6–10 px; subtle shadows only for overlays and raised controls. Thin borders separate content; do not nest every field in cards.

Typography: native sans stack; body 13 px, controls 12–13 px, page heading 20 px, section heading 14–16 px. Muted text still needs readable contrast. IDs and code can use monospace; metrics use tabular numerals. Avoid tiny uppercase labels and repeated hero titles.

Buttons: one blue primary per task group, bordered neutral secondary, quiet icon utilities. Destructive confirmation must remain visually distinct. Inputs use subtle neutral fills and visible focus rings. Disabled controls preserve legibility. Tooltips open on hover and keyboard focus and remain within viewport.

Charts use shared palette and theme tokens. Preserve units, legends and meaningful empty states. Motion is limited to short color/opacity transitions and respects reduced motion.

Avoid decorative gradients, navy dark panels, excessive blue pills, stacked action strips, oversized whitespace, nested card wallpaper, fake statistics, hidden focus, and color-only status.

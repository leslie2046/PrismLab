---
name: perflab-frontend-design
description: Design, implement, or review PrismLab frontend using its Dify-inspired workspace design system. Use for frontend layout, navigation, forms, datasets, tables, charts, feedback, responsiveness, and light/dark themes.
---

# PrismLab frontend design

Build a focused AI workspace inspired by Dify's information architecture and restrained controls. Preserve PrismLab's identity and real capabilities. Learn from the reference's hierarchy and interactions, not just its colors.

## Workflow

1. Inspect the affected component and shared CSS before editing. Preserve existing changes.
2. Read [visual-system.md](references/visual-system.md) for tokens, controls and themes, and [page-patterns.md](references/page-patterns.md) for page composition.
3. Separate the user's current task from configuration. Prefer object navigation such as **样本 / 评测记录 / 设置** over a page full of unrelated action buttons.
4. Update shared primitives first, then the affected page composition. Keep React, TypeScript, Vite, Tailwind and ECharts; do not add a UI framework for visual imitation.
5. Preserve APIs, permissions, localization, data semantics and chart behavior unless the user requests changes. Never invent metrics or Dify functionality.

## Design rules

- Neutral workspace sidebar, clear main content plane, restrained borders and saturated blue primary actions.
- Charcoal dark theme with the same hierarchy as light theme. Avoid blue-black surfaces, glass, gradients and decorative shadows.
- One page heading, one clear primary action, nearby search/filter utilities. Context belongs in breadcrumbs or a local rail.
- Dataset details separate samples from settings. Samples emphasize question/answer content; configuration uses grouped, labeled forms.
- Use Lucide icons, accessible labels, explicit focus and selected states. Status needs text as well as color.
- Explanations belong in hover/focus InfoTips. Expose important prerequisites/errors directly. Advanced fields can collapse.
- Preserve drafts during refresh and failed saves. Show saving, disabled, empty, loading and error states. Do not add polling to manually refreshed pages.
- Selection offers current-page selection, all matching records, clear selection and explicit bulk actions. Destructive operations retain confirmation and permissions.

## Verification

Run `npm run build`. Inspect meaningful interactions with mocked writes; do not mutate business data for QA. Check changed routes in both themes at 1440, 1024, 768 and 375 px. Verify keyboard access, selection, forms, dialogs, loading/empty/error/disabled states and overflow. Keep charts readable and wide tables deliberately scrollable. Report what was verified and any remaining limits.

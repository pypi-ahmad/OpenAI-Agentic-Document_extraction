<!-- okf:start -->
## Open Knowledge Format v0.2

Canonical governed project knowledge lives in `knowledge/index.md`.

- Read the index before architecture, policy, runbook, or domain work.
- Load only concepts relevant to the current task.
- Warn before relying on draft, deprecated, stale, or unverified concepts.
- Native instructions govern behavior; current source and tests govern factual conflicts.
<!-- okf:end -->

<!-- OPENWIKI:START -->

## OpenWiki

This repository has a generated `openwiki/` evidence index. It is optional just-in-time context, not required startup reading.

- Treat source code and tests as authoritative. A brief's unknowns and review items are verification gaps, not automatic requirements.
- Prefer the narrowest quiet validation that proves the changed behavior. Preserve complete failure output.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->

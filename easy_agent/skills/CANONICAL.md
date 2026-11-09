# Generated skill mirror — do not edit

This `easy_agent/skills/` tree is a **generated** copy of the repository-root
`skills/` directory. Do not edit files here: the next
`python scripts/skills/sync_skills.py --write` overwrites them and CI
(`python scripts/skills/sync_skills.py --check`) fails on any drift.

- Source of truth: `skills/` (canonical).
- Generated skills (7): docx, pptx, xlsx, pdf, mcp-builder, skill-creator,
  expert-creator — including the 12 shared office scripts under
  `docx|pptx|xlsx/scripts/office/`.
- Regenerate from the repo root: `python scripts/skills/sync_skills.py --write`.
- Verify: `python scripts/skills/sync_skills.py --check` (exit 1 on drift).
- `.agents/skills/` is development tooling, not part of this mirror.
- Design: `docs/archive/design/2026-10-18-a3-skills-single-source.md`.

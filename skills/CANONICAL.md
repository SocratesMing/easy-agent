# Canonical skills location

This repository-root `skills/` directory is the **only editable** runtime skill
tree (canonical). `easy_agent/skills/` is a generated, byte-for-byte mirror that
ships inside the package; never edit it by hand.

- Runtime discovery prefers `config.tools.skills_dir`, then this directory.
- Managed skills (7): `xlsx`, `pdf`, `microsoft-office`,
  `document-to-action-items`, `mcp-builder`, `skill-creator`, `expert-creator`.
  `microsoft-office` (Word, PowerPoint, Excel via python-docx / python-pptx /
  openpyxl) and `document-to-action-items` are MIT copies from
  NousResearch/hermes-agent. The Node `docx` and `pptx` skills were removed.
  Every file under these directories is hashed, including the 12 office scripts
  that remain in `xlsx/scripts/office/`.
- Edit here, then generate/verify the mirror from the repo root:

      python scripts/skills/sync_skills.py --check     # CI gate: drift -> exit 1
      python scripts/skills/sync_skills.py --write     # canonical -> packaged (one-way)
      python scripts/skills/sync_skills.py --manifest  # deterministic sha256 manifest

- Never sync backwards. If the packaged copy has content worth keeping, move it
  into this tree first, then run `--write`.
- Do not restore a second copy of the xlsx office validators under another
  skill name. `tests/skills/test_office_canonical.py` freezes the 12 scripts
  in `xlsx/scripts/office/`.
- Inconsistencies make CI fail (`sync_skills.py --check`); see
  `docs/archive/design/2026-10-18-a3-skills-single-source.md`.
- `.agents/skills/` is a separate tree of development-only agent skills. It is
  not runtime content and is not managed by this sync.

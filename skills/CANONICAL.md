# Canonical skills location

This repository-root `skills/` directory is the **only editable** runtime skill
tree (canonical). `easy_agent/skills/` is a generated, byte-for-byte mirror that
ships inside the package; never edit it by hand.

- Runtime discovery prefers `config.tools.skills_dir`, then this directory.
- Managed skills (7): `docx`, `pptx`, `xlsx`, `pdf`, `mcp-builder`,
  `skill-creator`, `expert-creator`. Every file under these directories is
  hashed, including the 12 shared office scripts in
  `docx|pptx|xlsx/scripts/office/`.
- Edit here, then generate/verify the mirror from the repo root:

      python scripts/skills/sync_skills.py --check     # CI gate: drift -> exit 1
      python scripts/skills/sync_skills.py --write     # canonical -> packaged (one-way)
      python scripts/skills/sync_skills.py --manifest  # deterministic sha256 manifest

- Never sync backwards. If the packaged copy has content worth keeping, move it
  into this tree first, then run `--write`.
- Do not add a third copy of `docx` / `pptx` / `xlsx` office validators: the
  three formats must share the same 12 office scripts (enforced by
  `tests/skills/test_office_canonical.py`).
- Inconsistencies make CI fail (`sync_skills.py --check`); see
  `docs/design/2026-10-18-a3-skills-single-source.md`.
- `.agents/skills/` is a separate tree of development-only agent skills. It is
  not runtime content and is not managed by this sync.

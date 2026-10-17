# Canonical skills location

This repository-root `skills/` directory is the canonical skill tree.

- Runtime discovery prefers `config.tools.skills_dir`, then this directory.
- `easy_agent/skills/` is a packaged fallback for installs that do not ship
  the repo-root tree. Edit office scripts here first, then sync the fallback
  copy if it must stay in lockstep.
- Do not add a third copy of `docx` / `pptx` / `xlsx` office validators.

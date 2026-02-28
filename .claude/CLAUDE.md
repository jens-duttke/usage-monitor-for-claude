# Project Guidelines

Apply Python best practices and clean code principles. Only change code relevant to the prompt.
Prioritize readability and auditability — users handle credentials and must be able to verify the code is safe at a glance.

## Security & Transparency
- All URLs and API endpoints as top-level constants — no dynamic URL construction
- Network communication exclusively with `api.anthropic.com` — no other destinations
- Credentials used only in HTTP Authorization headers — never log, store, or transmit elsewhere
- No file write operations — the app is read-only
- No `eval()`, `exec()`, `compile()`, or dynamic imports — no dynamic code execution
- No obfuscation — no base64-encoded strings, no encoded URLs or tokens
- Single-file architecture preferred — one file is easier to audit than many
- Exception: pure data files (translations, config) may be separate — they contain no logic or credential access
- Minimal, well-known dependencies only (e.g., requests, Pillow, pystray)

## Type Hints & Documentation
- Module docstring as very first element in file (title with equals underline, blank line, description)
- Always include `from __future__ import annotations` as first import (after module docstring)
- Type hints in function signatures only, not in docstrings
- numpydoc (NumPy-style) docstrings for all public functions, classes, and non-trivial methods
- Skip docstrings for trivial/self-explanatory methods (1-3 lines where the name fully describes the behavior)
- Never mention changes, improvements, or type hints in comments or docstrings
- `# type: ignore` only with specific error code and short reason: `# type: ignore[code]  # reason`

## Formatting
- PEP8-based with extended line length of 140-160 characters (flexible for arg parsing when alignment improves readability)
- Function signatures and calls on one line when reasonable
- Never use deep indentation to align with previous line's opening bracket/parenthesis
- When breaking lines, use standard 4-space indentation from statement start
- Single quotes (`'`) default, double (`"`) when containing single quotes, triple-double (`"""`) for docstrings

## Spacing
- Two blank lines between top-level functions/classes, one between methods
- Blank lines separate logical blocks (after guards, before returns)

## Imports
- Three groups separated by blank lines: standard library, third-party, local
- Within groups: `import` before `from...import`, sorted alphabetically
- Absolute imports, avoid wildcards, import NumPy as `np`

## Structure
- Main exported functions first, then helpers in logical order
- In library modules: prefix non-exported helpers with underscore; in executable scripts: no underscore prefix (everything is internal)
- `__all__` for library modules; omit for executable scripts

## Style
- Prefer functional/modular code over classes
- Pure functions without side effects
- Descriptive variable names, no global variables
- Comments only for complex/non-obvious code and math operations - never about improvements or changes

## List Comprehensions
- Avoid complex comprehensions with multiple conditions or long expressions
- Use explicit loops with guard clauses when: multiple conditions, repeated function calls per item, or unclear logic

## Validation & Errors
- Validate inputs at function start with assertions or exceptions
- Early returns and guard clauses

## PyInstaller / Build
- Spec file: `usage_monitor_for_claude.spec` — all build config lives there
- When adding new data files (translations, configs, assets): add them to the `datas` list in the spec file
- When adding new imports: check if PyInstaller detects them automatically; if not, add to `hiddenimports`
- Never exclude standard library modules that are transitive dependencies (e.g., `email` is needed by `urllib3`/`requests`)
- After any dependency change, verify the `excludes` list doesn't break transitive imports

## README
- Keep the feature list and descriptions in `README.md` in sync when adding, changing, or removing user-facing features

## Changelog
- Update `CHANGELOG.md` for every user-facing change (new features, bug fixes, behavior changes, UI changes)
- Do not add changelog entries for internal refactors, code style changes, or documentation-only changes unless they affect the user
- Changes to `CLAUDE.md` are invisible to users — never mention them in changelog entries or commit messages
- Add entries under the `## [Unreleased]` section, grouped by: Added, Changed, Fixed, Removed
- Write entries from the user's perspective — describe what changed, not how the code changed
- One bullet point per logical change; keep it concise (one sentence)

## Releasing
- Update `__version__` in `usage_monitor_for_claude.py` and all four version fields in `version_info.py` (`filevers`, `prodvers`, `FileVersion`, `ProductVersion`)
- In `CHANGELOG.md`: rename `## [Unreleased]` to `## [x.y.z] - YYYY-MM-DD`, add a fresh empty `## [Unreleased]` section above it, and update the compare links
- GitHub release notes (`gh release create --notes`) must use the exact content from the version's `CHANGELOG.md` section (the `### Added` / `### Changed` / `### Fixed` / `### Removed` blocks), followed by a `[Full changelog](compare-url)` link

## Execution
- Always activate virtual environment before running Python code
- Research current recommendations before changes if needed

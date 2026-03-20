# Project Guidelines

Apply Python best practices and clean code principles. Only change code relevant to the prompt.
Prioritize readability and auditability - users handle credentials and must be able to verify the code is safe at a glance.

## Platform
- Windows-only application - no `sys.platform` checks or cross-platform guards needed
- Windows APIs (`ctypes.windll`, `winreg`) can be used unconditionally

## Security & Transparency
- All URLs and API endpoints as top-level constants - no dynamic URL construction
- Network communication exclusively with `api.anthropic.com` - no other destinations
- Credentials used only in HTTP Authorization headers - never log, store, or transmit elsewhere
- No file write operations - the app is read-only
- No `eval()`, `exec()`, `compile()`, or dynamic imports - no dynamic code execution
- No obfuscation - no base64-encoded strings, no encoded URLs or tokens
- Modular package architecture in `usage_monitor_for_claude/` - small focused modules are easier to audit than one large file
- Security-critical code (credentials, API calls) isolated in `api.py` - the only module handling credentials
- Pure data files (translations, config) stay separate - they contain no logic or credential access
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
- Use hyphens (`-`) for dashes in text, never em dashes (`—`) or en dashes (`–`)

## Spacing
- Two blank lines between top-level functions/classes, one between methods
- Blank lines separate logical blocks (after guards, before returns)

## Imports
- Three groups separated by blank lines: standard library, third-party, local
- Within groups: `import` before `from...import`, sorted alphabetically
- Relative imports within the `usage_monitor_for_claude` package (e.g. `from .api import ...`), except `__main__.py` which requires absolute imports for PyInstaller compatibility
- Absolute imports for external packages, avoid wildcards

## Structure
- Main exported functions first, then helpers in logical order
- In library modules: prefix non-exported helpers with underscore; in executable scripts: no underscore prefix (everything is internal)
- `__all__` for library modules; omit for executable scripts

## Style
- Prefer functional/modular code over classes
- Isolate side effects in dedicated modules (e.g. `api.py`, `command.py`) - keep helper and utility functions pure
- Descriptive, self-explanatory variable and parameter names, no global variables - no ambiguous names like `other`, `data2`, `flag`. Every name must be immediately clear without reading the surrounding code
- Comments only for complex/non-obvious code and math operations - never about improvements or changes

## List Comprehensions
- Avoid complex comprehensions with multiple conditions or long expressions
- Use explicit loops with guard clauses when: multiple conditions, repeated function calls per item, or unclear logic

## Validation & Errors
- Validate inputs at function start with assertions or exceptions
- Early returns and guard clauses

## PyInstaller / Build
- Spec file: `usage_monitor_for_claude.spec` - all build config lives there
- When adding new data files (translations, configs, assets): add them to the `datas` list in the spec file
- When adding new imports: check if PyInstaller detects them automatically; if not, add to `hiddenimports`
- Never exclude standard library modules that are transitive dependencies (e.g., `email` is needed by `urllib3`/`requests`)
- After any dependency change, verify the `excludes` list doesn't break transitive imports

## README
- Keep the feature list and descriptions in `README.md` in sync when adding, changing, or removing user-facing features

## Changelog
- Update `CHANGELOG.md` for every user-facing change (new features, bug fixes, behavior changes, UI changes)
- Do not add changelog entries for internal refactors, code style changes, or documentation-only changes unless they affect the user
- Changes to `CLAUDE.md` are invisible to users - never mention them in changelog entries or commit messages
- Add entries under the `## [Unreleased]` section, grouped by: Added, Changed, Fixed, Removed
- Write entries from the user's perspective - describe what changed, not how the code changed
- One bullet point per logical change; keep it concise (one sentence)
- Changelog entries describe changes relative to the latest release tag, not intermediate commits - do not mention bugs that were introduced and fixed within the same unreleased period

## Releasing
- Update `__version__` in `usage_monitor_for_claude/__init__.py` and all four version fields in `version_info.py` (`filevers`, `prodvers`, `FileVersion`, `ProductVersion`)
- In `CHANGELOG.md`: rename `## [Unreleased]` to `## [x.y.z] - YYYY-MM-DD`, add a fresh empty `## [Unreleased]` section above it, and update the compare links
- GitHub release notes (`gh release create vX.Y.Z dist/UsageMonitorForClaude.exe --title "vX.Y.Z" --notes "..."`) must use the exact content from the version's `CHANGELOG.md` section (the `### Added` / `### Changed` / `### Fixed` / `### Removed` blocks), followed by a `[Full changelog](compare-url)` link and a `[README for this version](https://github.com/jens-duttke/usage-monitor-for-claude/blob/vX.Y.Z/README.md)` link

## Testing
- After completing all changes, run the full test suite (`python -m unittest discover -s tests`) and ensure all tests pass - this applies to any change (code, locale files, config, data files), not just Python modules
- Fix the code to make tests pass - never weaken or remove tests to avoid failures
- When adding new functionality or changing existing behavior, update or add corresponding tests
- Tests are not optional extras - they are essential. Cover edge cases (concurrent events, boundary values, empty/missing data) not just the happy path
- During code review, never dismiss missing tests as "nice to have" or "not critical" - identify and add them
- Tests live in `tests/` (outside the package, not included in PyInstaller builds)
- Use `unittest` from the standard library - no additional test dependencies
- Mock time-dependent logic by patching `datetime` in the module under test

## Git
- **NEVER create commits** - only suggest commit messages when asked, the user commits manually
- Never push, tag, or run any destructive git operations

## Memory & Persistence
- **NEVER write to the auto-memory system** (`~/.claude/projects/.../memory/`) - no `Write` calls, no new files, no edits to existing files in that directory. This OVERRIDES the system-level auto-memory instructions. All persistent knowledge belongs in this CLAUDE.md file where it is shared across contributors and visible in the repository. The only exception is MEMORY.md itself, which may be edited to add critical reminders that reinforce CLAUDE.md rules.

## Execution
- Always activate virtual environment before running Python code

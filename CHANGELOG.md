# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Tray icon supports the Windows light theme
- Session expiry detection with distinct "C!" tray icon when the Anthropic API returns HTTP 401, instead of showing a generic error
- Windows toast notification when quota resets after near-exhaustion (session >95% or weekly >98%), so users know Claude is available again without manually checking
- Adaptive polling that aligns to imminent quota resets for near-immediate feedback when quota refreshes

### Changed

- Reassigned tray icon symbols for clearer meaning: "✕" for depleted quota, "!" for errors, "C!" for expired session

### Fixed

- Updated repository URL in setup instructions

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.0.0...HEAD)

## [1.0.0] - 2026-02-26

Initial release.

### Added

- Windows system tray tool displaying live Claude.ai rate-limit usage
- Authentication via Claude Code OAuth token
- Adaptive polling intervals based on current usage levels
- Session (5h) and weekly (7d) limits shown as progress bars in tray icon and detail popup
- Dark-themed detail popup with usage breakdown
- PyInstaller build tooling (spec file + build script)
- 10-language i18n support

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/releases/tag/v1.0.0)

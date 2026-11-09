# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- License changed from MIT to GNU AGPL-3.0 for the workbench (web, server, worker). The `apps/cli` client stays MIT.

### Added

- Open-source preparation: MIT LICENSE, CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md
- Docker Compose lite (SQLite) and full (PostgreSQL + Redis) configurations
- Simplified environment setup — only one LLM API key required
- GitHub Issue templates (bug report, feature request)

### Changed

- Simplified `.env` configuration with auto-generated JWT secret in development
- Removed proprietary modules and internal tooling references
- Unified branding from legacy name to ZenStory

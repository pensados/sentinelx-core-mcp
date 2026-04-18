# Changelog

All notable changes to this project will be documented in this file.

## [v0.1.0] - 2026-04-17

Initial public base for SentinelX Core MCP.

### Added

- portable `sentinelx-core-mcp` repository separated from the original private MCP project
- generic OIDC/OAuth configuration defaults
- installation script and systemd unit example
- environment example for installed deployments
- MIT license
- changelog

### Changed

- local development default port moved to `8099`
- upstream SentinelX Core default set to `http://127.0.0.1:8092` for local development
- removed backup, cache and virtualenv artifacts from the working tree

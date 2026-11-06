# dreame-mcp — Agent Guide

## Overview
FastMCP 3.2.0 MCP server and webapp for Dreame robot vacuums via DreameHome cloud

## Standards
- FastMCP 3.2+ portmanteau tool pattern — tools use `operation` enum param
- Responses: structured dicts with `success`, `message`, domain-specific fields
- Dual transport: stdio (Claude Desktop) + HTTP (`MCP_TRANSPORT=http`)
- See [mcp-central-docs](https://github.com/sandraschi/mcp-central-docs) for fleet-wide coding standards

## Key Files
- `README.md` — full documentation
- `pyproject.toml` — build config and entry points
- `CLAUDE.md` — Claude Code context (if present)

Install docs: follow mcp-central-docs/standards/AGENT_INSTALL_REFERENCE.md

## Living skills

This fork keeps its own project skills in `.claude/skills/`. When you learn
something non-obvious here (a fix, a pitfall, a procedure that took more than
one try), capture it in the matching skill **in the same commit or pull
request**, or add a new skill if none fits. Update a skill when what it says
stops being true, and prune what is no longer needed. Keep each skill small
and to one task: a precise description of what it does and when to use it, a
short `SKILL.md`, and links instead of copied text. Current skills:

- `voice-plugin`: the vacuum's voice plugin for the claude-voice bridge,
  how it is wired, approved and changed safely.
- `robot-map`: get, show and debug the vacuum's map, where it is, and a
  short film of it moving.

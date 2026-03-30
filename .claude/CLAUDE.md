# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an AI-powered newsletter generation system that:
1. Fetches AI-related emails from Gmail using IMAP (filtered by 'ai_news' label)
2. Uses AI models to generate summaries and select relevant content
3. Creates HTML newsletters with generated images
4. Sends newsletters via SMTP to subscribers
5. Stores newsletter data in PostgreSQL database

## Architecture

- **main.py**: Entry point with command parsing for daily/weekly newsletters
- **gmail.py**: IMAP email fetching and processing (Mail class)
- **ai.py**: AI content generation using JustAI library (summaries, images)
- **formatter.py**: HTML email template generation
- **mailer.py**: SMTP newsletter sending with logging
- **database.py**: PostgreSQL database operations using SQLAlchemy
- **subscribers.py**: Subscriber management
- **s3.py**: S3 integration for image storage

## Dependencies

- **justai**: Custom AI wrapper library for multiple LLM providers
- **justdays**: Custom date/time utility library
- **sqlalchemy**: Database ORM
- **psycopg2-binary**: PostgreSQL adapter
- **boto3**: AWS S3 integration
- **python-dotenv**: Environment variable management

## Commands

- **Run newsletter**: `python main.py daily` or `python main.py weekly`
- **Use cached data**: `python main.py daily --cached` (skips email fetching)
- **Install dependencies**: `pip install -r requirements.txt`

## Configuration

Requires `.env` file with:
- EMAIL_HOST_USER, EMAIL_HOST_PASSWORD: Gmail credentials
- EMAIL_IMAP_SERVER, EMAIL_IMAP_PORT: IMAP settings
- EMAIL_HOST, EMAIL_PORT: SMTP settings
- DATABASE_URL: PostgreSQL connection string
- AWS credentials for S3 image storage

## AI Models Used

- **Copy writing**: Gemini 2.5 Pro (Dutch newsletter content)
- **Art direction**: GPT-5 (image concept generation)
- **Image generation**: Gemini 2.5 Flash Image Preview

## Data Storage

- **cache/**: Cached AI responses and email data
- **data/**: Runtime data (last_sent.json, mailerlog.txt, logo.png)
- Database stores newsletter content and metadata

## Newsletter Process

1. Parse command line (daily/weekly, --cached flag)
2. Fetch raw emails from Gmail with ai_news label
3. Generate AI summary from email content (Dutch language)
4. Create newsletter image using AI art direction + generation
5. Format as HTML email with branding
6. Store in database and send to subscribers via SMTP
7. Log delivery and track sent emails

## Content Style

The AI generates content in Dutch, following specific style guidelines:
- Professional but accessible tone for business/tech readers
- Evidence-first approach with concrete dates and numbers
- Actionable insights for readers
- Critical perspective on AI developments while remaining optimistic

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **ainews** (136 symbols, 375 relationships, 20 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## When Debugging

1. `gitnexus_query({query: "<error or symptom>"})` — find execution flows related to the issue
2. `gitnexus_context({name: "<suspect function>"})` — see all callers, callees, and process participation
3. `READ gitnexus://repo/ainews/process/{processName}` — trace the full execution flow step by step
4. For regressions: `gitnexus_detect_changes({scope: "compare", base_ref: "main"})` — see what your branch changed

## When Refactoring

- **Renaming**: MUST use `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` first. Review the preview — graph edits are safe, text_search edits need manual review. Then run with `dry_run: false`.
- **Extracting/Splitting**: MUST run `gitnexus_context({name: "target"})` to see all incoming/outgoing refs, then `gitnexus_impact({target: "target", direction: "upstream"})` to find all external callers before moving code.
- After any refactor: run `gitnexus_detect_changes({scope: "all"})` to verify only expected files changed.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Tools Quick Reference

| Tool | When to use | Command |
|------|-------------|---------|
| `query` | Find code by concept | `gitnexus_query({query: "auth validation"})` |
| `context` | 360-degree view of one symbol | `gitnexus_context({name: "validateUser"})` |
| `impact` | Blast radius before editing | `gitnexus_impact({target: "X", direction: "upstream"})` |
| `detect_changes` | Pre-commit scope check | `gitnexus_detect_changes({scope: "staged"})` |
| `rename` | Safe multi-file rename | `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` |
| `cypher` | Custom graph queries | `gitnexus_cypher({query: "MATCH ..."})` |

## Impact Risk Levels

| Depth | Meaning | Action |
|-------|---------|--------|
| d=1 | WILL BREAK — direct callers/importers | MUST update these |
| d=2 | LIKELY AFFECTED — indirect deps | Should test |
| d=3 | MAY NEED TESTING — transitive | Test if critical path |

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/ainews/context` | Codebase overview, check index freshness |
| `gitnexus://repo/ainews/clusters` | All functional areas |
| `gitnexus://repo/ainews/processes` | All execution flows |
| `gitnexus://repo/ainews/process/{name}` | Step-by-step execution trace |

## Self-Check Before Finishing

Before completing any code modification task, verify:
1. `gitnexus_impact` was run for all modified symbols
2. No HIGH/CRITICAL risk warnings were ignored
3. `gitnexus_detect_changes()` confirms changes match expected scope
4. All d=1 (WILL BREAK) dependents were updated

## Keeping the Index Fresh

After committing code changes, the GitNexus index becomes stale. Re-run analyze to update it:

```bash
npx gitnexus analyze
```

If the index previously included embeddings, preserve them by adding `--embeddings`:

```bash
npx gitnexus analyze --embeddings
```

To check whether embeddings exist, inspect `.gitnexus/meta.json` — the `stats.embeddings` field shows the count (0 means no embeddings). **Running analyze without `--embeddings` will delete any previously generated embeddings.**

> Claude Code users: A PostToolUse hook handles this automatically after `git commit` and `git merge`.

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->

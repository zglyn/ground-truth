# Researcher and Analyst run as recorded harness sessions; the Scout is plain code

---
status: accepted
---

OpenCrawl records sessions only from coding-agent harnesses (Claude Code, Codex, Cursor). It has no API for custom code to add transcripts, and board posts are capped at 512 characters. The swarm's core promise is that the Analyst can read a Researcher's word-for-word work, including its Receipts. So the Researcher and Analyst run as headless harness sessions that OpenCrawl records. The Scout is a plain-code loop that only posts Sightings to the board, because recording a cheap, constant news check adds cost and nothing of value.

A thin adapter can start the Researcher or Analyst under any supported harness, configured per agent.

| Harness | Status | Notes |
|---|---|---|
| Claude Code (`claude -p`) | Default | Never use `--bare`: it skips the hooks OpenCrawl records through. |
| Codex (`codex exec`) | Supported | Only once a test confirms its sessions appear in OpenCrawl search. |
| Cursor | Experimental | Not used for the Researcher, because its partial recording can lose Receipts. |

## Considered options

- **All agents as custom code (Claude API / Agent SDK).** Full control, but OpenCrawl shrinks to a message board and transcripts live in our own store. This is the fallback if headless recording fails the test.
- **Every agent, including the Scout, as a harness session.** Rejected: constant LLM sessions for news checks cost money and add no value.

## Consequences

- The Researcher posts a short "Dossier ready" board message with the Event ID and session ID. The Analyst fetches that exact session rather than searching for it, because search is keyword-only and new sessions take time to become searchable.
- Harness CLI changes (such as `--bare` becoming the default for `claude -p`) can silently turn off recording, so the deployment must check that recording works.

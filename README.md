# CompetitiveIntelligenceMonitorAgent

Twice a day, searches public news sources for potential competitors, batches
everything found into a single summary, and asks a human on Momentum Feed to
approve sending that summary as an email digest. Separately, an operator can
manually confirm a specific competitor name to trigger deeper research, with
results stored locally for later querying.

This agent is **informational only**: it never performs an irreversible action
on its own. It reports facts via `emit_event`. Sending the digest email is
gated behind a single `request_approval` call per run — the agent's process
blocks on that call until the operator resolves it on Momentum Feed:

- **Approved** — the digest email is sent to `DIGEST_EMAIL_TO`.
- **Rejected** — no email is sent; the run is logged and moved past.

Because `request_approval` blocks the calling thread, an unresolved
escalation pauses the loop until someone acts on it in Momentum Feed. There's
only one such call per run now (not one per article), so this is a single
pause point twice a day rather than one per finding.

## Setup

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
2. Copy the environment template:
   ```
   cp .env.example .env
   ```
3. Fill in `.env` with your real values. Follow `HOW_TO_GET_CREDENTIALS.md` for
   step-by-step instructions on obtaining `FORCEEQUALS_API_KEY`,
   `FORCEEQUALS_AGENT_ID`, and SMTP credentials for sending the digest.

## Schedule

Runs twice daily at the times set by `COMPETITOR_RUN_TIMES` (default
`09:00,17:00`) in the timezone set by `COMPETITOR_TIMEZONE` (default
`America/New_York`, i.e. Tampa, FL). Change either in `.env` if you want a
different cadence or timezone.

## Running the agent

Start the scheduler:

```
python agent.py run
```

Each run:
1. Searches Google News RSS for the configured terms.
2. Skips anything already reported in a previous run.
3. If nothing new is found, logs that and waits for the next scheduled time.
4. If something new is found, builds one summary (article count + whether
   any name looks new) and emits it as `competitor.run.summary` to Feed.
5. Requests approval for that single run. Once approved, sends the digest
   email; once rejected, skips the email.

### On "new competitor found"

There's no real entity extraction here — Google News RSS returns headlines,
not clean company names. The agent strips the `" - Publisher"` suffix Google
adds and tracks those normalized headline-derived names across runs in
`known_competitor_names.json`. A name not seen before is flagged as
"new." This is a heuristic and will sometimes flag a headline variation as
"new" even if it's the same company, or miss a genuinely new competitor whose
headline phrasing happens to match something seen before.

## Confirming a competitor (manual, separate from the schedule)

When you decide a name from a digest is a real competitor, run:

```
python agent.py confirm --name "Acme Corp"
```

This triggers deeper research, emits a `competitor.insight` event (escalating
to a human approval if a guardrail blocks it), stores the findings in
`insights_store.json`, and marks the name as known so it won't be flagged as
"new" again.

## Querying stored insights

```
python agent.py query --name "Acme Corp"
```

## Files

- `agent.py` — scheduler, connector, summary/approval flow, and email digest
- `requirements.txt` — Python dependencies
- `.env.example` — credential and configuration template
- `HOW_TO_GET_CREDENTIALS.md` — step-by-step credential walkthrough
- `seen_items.json` / `known_competitor_names.json` / `insights_store.json` —
  created automatically at runtime

## Credentials

See `HOW_TO_GET_CREDENTIALS.md` for exactly what you need and how to get it.

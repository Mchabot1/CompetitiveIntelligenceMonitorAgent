# How to get every credential for CompetitiveIntelligenceMonitorAgent

You need **3** secrets. Do them in this order. After each one, paste the value into a local `.env` file (copy from `.env.example`). Never upload `.env` to GitHub.

## 1 of 3 — `FORCEEQUALS_API_KEY`
Lets this agent talk to Momentum (Feed cards and approvals).

1. Open ForceEquals Momentum in your browser and sign in.
2. In the left menu, click **API Key**.
3. Click **Generate** (or Create key).
4. Copy the value. It starts with `fe_live_` and is shown **once**. Paste it into a notes app immediately.
5. Later paste that same value into `.env` as `FORCEEQUALS_API_KEY`. Never put it on GitHub.

## 2 of 3 — `FORCEEQUALS_AGENT_ID`
Must match the agent you register on Feed, or nothing appears on Feed.

1. In Momentum, open **Feed** (left menu).
2. Click the round **+ Add Agent** button.
3. Choose **Code agent** (not no-code).
4. Name it **CompetitiveIntelligenceMonitorAgent**. Prefer the id `competitive-intelligence-monitor-agent`. If Momentum assigns a different id, copy that one instead — it wins.
5. Finish / Connect.
6. Paste the id into `.env` as `FORCEEQUALS_AGENT_ID`.

## 3 of 3 — `SMTP_USERNAME` and `SMTP_PASSWORD`
Lets the agent send the approved digest by email. Use whatever provider you already send mail through. Two common options:

**Option A — Gmail with an App Password**
1. Go to your Google Account → **Security**.
2. Turn on 2-Step Verification if it isn't already on.
3. Under "How you sign in to Google," open **App passwords**.
4. Create one for "Mail" and copy the 16-character password.
5. In `.env`, set:
   ```
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USERNAME=your_gmail_address@gmail.com
   SMTP_PASSWORD=the_16_char_app_password
   ```

**Option B — A transactional email provider (SendGrid, Postmark, etc.)**
1. Sign in to that provider's dashboard.
2. Create an SMTP API key / SMTP credential (not a general API key — look for "SMTP" specifically).
3. Copy the SMTP host, port, username, and password/key it gives you.
4. Put those exact values into `.env` as `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`.

## Done when
- [ ] `CompetitiveIntelligenceMonitorAgent` has a real `agent.py` and `requirements.txt`
- [ ] All **3** secrets above have a real value in `.env`
- [ ] `.env.example` lists the same names with blank/placeholder values
- [ ] No real secret is in git, README, or this how-to file

## Note on connector credentials
This agent's news connector uses a public Google News RSS search feed, which does not require an API key. No additional connector secrets are needed beyond the 3 listed above.

## Non-secret settings (also in `.env`, but not credentials)
- `DIGEST_EMAIL_TO` — who receives the digest (defaults to `marc@forceequals.com`)
- `DIGEST_EMAIL_FROM` — the From address on the digest (defaults to `SMTP_USERNAME` if left blank)
- `COMPETITOR_RUN_TIMES` — the two daily run times, e.g. `09:00,17:00`
- `COMPETITOR_TIMEZONE` — IANA timezone name for those run times, e.g. `America/New_York`

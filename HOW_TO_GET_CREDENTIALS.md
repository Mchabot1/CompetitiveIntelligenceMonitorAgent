# How to get every credential for CompetitiveIntelligenceMonitorAgent

You need **2** secrets. Do them in this order. After each one, paste the value into a local `.env` file (copy from `.env.example`). Never upload `.env` to GitHub.

## 1 of 2 — `FORCEEQUALS_API_KEY`
Lets this agent talk to Momentum (Feed cards and approvals).

1. Open ForceEquals Momentum in your browser and sign in.
2. In the left menu, click **API Key**.
3. Click **Generate** (or Create key).
4. Copy the value. It starts with `fe_live_` and is shown **once**. Paste it into a notes app immediately.
5. Later paste that same value into `.env` as `FORCEEQUALS_API_KEY`. Never put it on GitHub.

## 2 of 2 — `FORCEEQUALS_AGENT_ID`
Must match the agent you register on Feed, or nothing appears on Feed.

1. In Momentum, open **Feed** (left menu).
2. Click the round **+ Add Agent** button.
3. Choose **Code agent** (not no-code).
4. Name it **CompetitiveIntelligenceMonitorAgent**. Prefer the id `competitive-intelligence-monitor-agent`. If Momentum assigns a different id, copy that one instead — it wins.
5. Finish / Connect.
6. Paste the id into `.env` as `FORCEEQUALS_AGENT_ID`.

## Done when
- [ ] `CompetitiveIntelligenceMonitorAgent` has a real `agent.py` and `requirements.txt`
- [ ] All **2** names above have a real value in `.env`
- [ ] `.env.example` lists the same **2** names with blank values
- [ ] No real secret is in git, README, or this how-to file

## Note on connector credentials
This agent's news connector uses a public Google News RSS search feed, which does not require an API key. No additional connector secrets are needed beyond the 2 listed above.

"""
CompetitiveIntelligenceMonitorAgent
------------------------------------
Twice daily, searches public news sources for potential competitors,
batches everything found into a single per-run summary, and asks a
human on Momentum Feed to approve sending that summary as an email
digest to the operator. Separately, an operator can manually confirm
a specific competitor to trigger deeper research, whose insights are
stored locally for future querying.

This agent takes no irreversible action on its own. It reports facts via
fe.emit_event(...). Sending the digest email only happens after a human
approves it via fe.request_approval(...) on Momentum Feed. If a guardrail
blocks the summary event itself, that's logged but does not block the
approval request — approval is the real gate on the email side effect.

Usage:
    python agent.py run                                  # start the twice-daily loop
    python agent.py confirm --name "Acme Corp"           # operator confirms a competitor
    python agent.py query --name "Acme Corp"             # print stored insights
"""

import argparse
import hashlib
import html
import json
import logging
import os
import smtplib
import sys
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import feedparser

from forceequals import ForceEquals, GovernanceBlockedError, GovernanceRejectedError

# ---------------------------------------------------------------------------
# ForceEquals SDK setup (required snippet — do not modify the shape of this)
# ---------------------------------------------------------------------------

fe = ForceEquals(
    api_key=os.getenv("FORCEEQUALS_API_KEY"),
    agent_id=os.getenv("FORCEEQUALS_AGENT_ID", "competitive-intelligence-monitor-agent"),
)


@fe.governed
def handle_run(payload: dict) -> dict:
    """
    One business run = one scan cycle. Reports a summary fact, then asks
    a human to approve emailing the digest.
    """
    try:
        fe.emit_event("competitor.run.summary", payload)
    except GovernanceBlockedError as exc:
        # Informational event blocked — log it, but don't let it stop the
        # approval request below. The email is the real action being gated.
        log.warning("Summary event blocked by governance: %s", exc)

    fe.request_approval(
        title=(
            f"Competitor scan: {payload['total_articles_found']} article(s) found"
            + (" — NEW competitor detected" if payload["new_competitor_found"] else " — no new competitors")
        ),
        context=payload,
    )
    return {"ok": True}


@fe.governed
def handle_confirmed_case(payload: dict) -> dict:
    try:
        fe.emit_event("competitor.insight", payload)
    except GovernanceBlockedError as exc:
        # Escalate to a human instead of silently dropping it.
        fe.request_approval(
            title=f"Guardrail blocked insight for: {payload.get('name', 'unknown')}",
            context={"reason": str(exc), "payload": payload},
        )
        fe.emit_event("competitor.insight", payload)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE = os.path.join(DATA_DIR, "seen_items.json")
INSIGHTS_FILE = os.path.join(DATA_DIR, "insights_store.json")
KNOWN_NAMES_FILE = os.path.join(DATA_DIR, "known_competitor_names.json")

# Twice-daily schedule. Override with COMPETITOR_RUN_TIMES="08:00,20:00" etc.
DEFAULT_RUN_TIMES = ["09:00", "17:00"]
RUN_TIMES = [
    t.strip()
    for t in os.getenv("COMPETITOR_RUN_TIMES", "").split(",")
    if t.strip()
] or DEFAULT_RUN_TIMES
TIMEZONE_NAME = os.getenv("COMPETITOR_TIMEZONE", "America/New_York")  # Tampa, FL

DEFAULT_SEARCH_TERMS = [
    "AI agent governance platform",
    "AI agent operations platform",
    "AI agent orchestration startup",
]
SEARCH_TERMS = [
    term.strip()
    for term in os.getenv("COMPETITOR_SEARCH_TERMS", "").split(",")
    if term.strip()
] or DEFAULT_SEARCH_TERMS

GOOGLE_NEWS_RSS_TEMPLATE = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

# Email digest settings.
DIGEST_EMAIL_TO = os.getenv("DIGEST_EMAIL_TO", "marc@forceequals.com")
DIGEST_EMAIL_FROM = os.getenv("DIGEST_EMAIL_FROM")  # falls back to SMTP_USERNAME if unset
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() != "false"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("competitive-intelligence-monitor-agent")


# ---------------------------------------------------------------------------
# Local persistence helpers
# ---------------------------------------------------------------------------

def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        log.warning("Could not read %s, starting fresh.", path)
        return default


def save_json(path: str, data) -> None:
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def make_item_id(source_url: str) -> str:
    return hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:16]


def normalize_candidate_name(raw_title: str) -> str:
    """
    Google News RSS titles are usually "Headline - Publisher". Strip the
    publisher suffix to get a rougher approximation of the subject. This is
    a heuristic, not real entity extraction — it will sometimes be wrong.
    """
    parts = raw_title.rsplit(" - ", 1)
    candidate = parts[0] if len(parts) == 2 else raw_title
    return candidate.strip().lower()


# ---------------------------------------------------------------------------
# Connector: public news search (no API key required)
# ---------------------------------------------------------------------------

def fetch_potential_competitors(search_terms):
    """
    Polls a public Google News RSS search feed for each search term.
    Returns a list of candidate dicts. No connector credentials required.
    """
    candidates = []
    for term in search_terms:
        query = term.replace(" ", "+")
        url = GOOGLE_NEWS_RSS_TEMPLATE.format(query=query)
        try:
            parsed = feedparser.parse(url)
        except Exception as exc:  # network hiccups shouldn't kill the loop
            log.warning("Feed fetch failed for term '%s': %s", term, exc)
            continue

        for entry in parsed.entries:
            link = getattr(entry, "link", None)
            title = getattr(entry, "title", None)
            if not link or not title:
                continue
            summary = getattr(entry, "summary", "") or ""
            published = getattr(entry, "published", None)
            candidates.append(
                {
                    "id": make_item_id(link),
                    "name": title,
                    "candidate_name": normalize_candidate_name(title),
                    "source": "google_news_rss",
                    "search_term": term,
                    "mention_url": link,
                    "date_found": datetime.now(timezone.utc).isoformat(),
                    "published": published,
                    "summary": summary,
                }
            )
    return candidates


def fetch_deep_research(name: str):
    """
    Performs a broader search on a confirmed competitor name and compiles
    an insights payload matching the competitor.insight schema.
    """
    query = name.replace(" ", "+")
    url = GOOGLE_NEWS_RSS_TEMPLATE.format(query=query)
    try:
        parsed = feedparser.parse(url)
    except Exception as exc:
        log.warning("Deep research fetch failed for '%s': %s", name, exc)
        parsed = None

    related_links = []
    insight_lines = []
    if parsed is not None:
        for entry in parsed.entries[:10]:
            link = getattr(entry, "link", None)
            title = getattr(entry, "title", None)
            if not link or not title:
                continue
            related_links.append(link)
            insight_lines.append(title)

    confidence = "high" if len(related_links) >= 5 else ("medium" if related_links else "low")

    return {
        "name": name,
        "insights": insight_lines,
        "related_links": related_links,
        "date": datetime.now(timezone.utc).isoformat(),
        "confidence": confidence,
    }


# ---------------------------------------------------------------------------
# Email digest
# ---------------------------------------------------------------------------

def build_digest_subject(payload: dict) -> str:
    return (
        f"Competitor scan: {payload['total_articles_found']} article(s)"
        + (" — NEW competitor detected" if payload["new_competitor_found"] else "")
    )


def build_digest_plain_text(payload: dict) -> str:
    lines = [f"Competitor scan run at {payload['run_time']}", ""]
    if payload["new_competitor_found"]:
        lines.append("New competitor name(s) detected this run:")
        for name in payload["new_competitor_names"]:
            lines.append(f"  - {name}")
        lines.append("")
    else:
        lines.append("No new competitor names detected this run.")
        lines.append("")

    lines.append(f"Total articles found: {payload['total_articles_found']}")
    lines.append("")
    lines.append("Findings:")
    for item in payload["items"]:
        lines.append(f"- {item['name']}")
        lines.append(f"    Link: {item['mention_url']}")
        if item.get("summary"):
            lines.append(f"    Summary: {item['summary']}")
        lines.append("")

    return "\n".join(lines)


def build_digest_html(payload: dict) -> str:
    """
    Builds an HTML email body using table-based layout and inline styles
    only — no CSS variables, flexbox, or grid, since mail clients (Outlook
    in particular) don't reliably support those.
    """
    e = html.escape  # shorthand

    if payload["new_competitor_found"]:
        names_html = "".join(
            f'<p style="margin:2px 0 0; font-size:13px; color:#663f00;">{e(name)}</p>'
            for name in payload["new_competitor_names"]
        )
        new_competitor_block = f"""
        <tr>
          <td style="padding:16px 24px 0;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#fdf3e0; border:1px solid #f0c987; border-radius:6px;">
              <tr>
                <td style="padding:10px 14px;">
                  <p style="margin:0; font-size:13px; font-weight:bold; color:#8a5a00;">New competitor name(s) detected this run</p>
                  {names_html}
                </td>
              </tr>
            </table>
          </td>
        </tr>
        """
    else:
        new_competitor_block = """
        <tr>
          <td style="padding:16px 24px 0;">
            <p style="margin:0; font-size:13px; color:#666666;">No new competitor names detected this run.</p>
          </td>
        </tr>
        """

    finding_blocks = []
    for item in payload["items"]:
        summary_html = ""
        if item.get("summary"):
            summary_html = (
                f'<p style="margin:4px 0 0; font-size:13px; color:#666666;">{e(item["summary"])}</p>'
            )
        finding_blocks.append(f"""
        <tr>
          <td style="padding-top:12px; border-top:1px solid #eeeeee;">
            <p style="margin:0 0 2px; font-size:14px; color:#1a1a1a;">{e(item['name'])}</p>
            <a href="{e(item['mention_url'])}" style="font-size:12px; color:#1a73e8; word-break:break-all;">{e(item['mention_url'])}</a>
            {summary_html}
          </td>
        </tr>
        """)
    findings_html = "".join(finding_blocks)

    subject = e(build_digest_subject(payload))
    run_time = e(payload["run_time"])
    total = payload["total_articles_found"]

    return f"""\
<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
  </head>
  <body style="margin:0; padding:0; background-color:#f4f4f4; font-family: Arial, Helvetica, sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f4f4; padding:24px 0;">
      <tr>
        <td align="center">
          <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="background-color:#ffffff; border:1px solid #e0e0e0; border-radius:8px;">
            <tr>
              <td style="padding:20px 24px; border-bottom:1px solid #e0e0e0;">
                <p style="margin:0; font-size:16px; font-weight:bold; color:#1a1a1a;">{subject}</p>
                <p style="margin:4px 0 0; font-size:13px; color:#666666;">Run at {run_time}</p>
              </td>
            </tr>
            {new_competitor_block}
            <tr>
              <td style="padding:16px 24px 0;">
                <p style="margin:0; font-size:13px; color:#666666;">Total articles found: <strong style="color:#1a1a1a;">{total}</strong></p>
              </td>
            </tr>
            <tr>
              <td style="padding:16px 24px 24px;">
                <p style="margin:0 0 8px; font-size:13px; font-weight:bold; color:#1a1a1a;">Findings</p>
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                  {findings_html}
                </table>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


def build_digest_email(payload: dict) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = build_digest_subject(payload)
    msg["From"] = DIGEST_EMAIL_FROM or SMTP_USERNAME
    msg["To"] = DIGEST_EMAIL_TO
    # Plain-text fallback first, HTML alternative second — mail clients
    # capable of HTML render the last (HTML) part; others fall back to text.
    msg.set_content(build_digest_plain_text(payload))
    msg.add_alternative(build_digest_html(payload), subtype="html")
    return msg


def send_digest_email(payload: dict) -> bool:
    if not SMTP_HOST or not SMTP_USERNAME or not SMTP_PASSWORD:
        log.error(
            "Cannot send digest email: SMTP_HOST, SMTP_USERNAME, or SMTP_PASSWORD is not set."
        )
        return False

    msg = build_digest_email(payload)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            if SMTP_USE_TLS:
                server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
        log.info("Digest email sent to %s.", DIGEST_EMAIL_TO)
        return True
    except Exception as exc:
        log.error("Failed to send digest email: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Scheduling (twice daily at fixed local times)
# ---------------------------------------------------------------------------

def compute_next_run(run_times, tz_name):
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    candidates = []
    for t in run_times:
        hh, mm = (int(x) for x in t.split(":"))
        candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        candidates.append(candidate)
    next_run = min(candidates)
    return next_run, (next_run - now).total_seconds()


# ---------------------------------------------------------------------------
# One scan cycle
# ---------------------------------------------------------------------------

def perform_scan(seen_ids: set, known_names: set):
    try:
        candidates = fetch_potential_competitors(SEARCH_TERMS)
    except Exception as exc:
        log.error("Error during discovery: %s", exc)
        candidates = []

    new_items = [c for c in candidates if c["id"] not in seen_ids]

    if not new_items:
        log.info("No new potential competitors found this cycle.")
        return seen_ids, known_names

    new_competitor_names = sorted(
        {item["candidate_name"] for item in new_items if item["candidate_name"] not in known_names}
    )

    summary_payload = {
        "run_time": datetime.now(timezone.utc).isoformat(),
        "total_articles_found": len(new_items),
        "new_competitor_found": bool(new_competitor_names),
        "new_competitor_names": new_competitor_names,
        "items": [
            {
                "name": item["name"],
                "source": item["source"],
                "mention_url": item["mention_url"],
                "summary": item["summary"],
                "date_found": item["date_found"],
            }
            for item in new_items
        ],
    }

    # Mark everything found this run as seen and known, regardless of the
    # approval outcome — the finding itself has been reported either way.
    seen_ids = seen_ids | {item["id"] for item in new_items}
    known_names = known_names | {item["candidate_name"] for item in new_items}
    save_json(SEEN_FILE, sorted(seen_ids))
    save_json(KNOWN_NAMES_FILE, sorted(known_names))

    try:
        handle_run(summary_payload)
    except GovernanceRejectedError as exc:
        log.warning("Operator rejected the digest email for this run: %s", exc)
        return seen_ids, known_names
    except GovernanceBlockedError as exc:
        log.warning("Run summary blocked by governance and not resolved: %s", exc)
        return seen_ids, known_names
    except Exception as exc:
        log.error("Unexpected error handling run summary: %s", exc)
        return seen_ids, known_names

    # Approved — send the digest.
    send_digest_email(summary_payload)
    return seen_ids, known_names


# ---------------------------------------------------------------------------
# Main loop (twice daily)
# ---------------------------------------------------------------------------

def run_loop():
    log.info(
        "Starting CompetitiveIntelligenceMonitorAgent. Scheduled at %s (%s).",
        RUN_TIMES,
        TIMEZONE_NAME,
    )
    seen_ids = set(load_json(SEEN_FILE, []))
    known_names = set(load_json(KNOWN_NAMES_FILE, []))

    while True:
        next_run, wait_seconds = compute_next_run(RUN_TIMES, TIMEZONE_NAME)
        log.info(
            "Next scan scheduled for %s (sleeping %.0f seconds).",
            next_run.isoformat(),
            wait_seconds,
        )
        time.sleep(max(wait_seconds, 0))
        seen_ids, known_names = perform_scan(seen_ids, known_names)


# ---------------------------------------------------------------------------
# Operator-triggered actions (confirm / query)
# ---------------------------------------------------------------------------

def confirm_competitor(name: str):
    """
    Called by the operator after reviewing findings and deciding a name is
    a real competitor. Performs deeper research and emits a competitor.insight
    event, then stores the result locally.
    """
    log.info("Operator confirmed competitor: %s. Running deep research...", name)
    insight_payload = fetch_deep_research(name)

    try:
        handle_confirmed_case(insight_payload)
    except GovernanceRejectedError as exc:
        log.warning("Human rejected escalation for insight on '%s': %s", name, exc)
        return
    except GovernanceBlockedError as exc:
        log.warning("Insight event blocked by governance for '%s': %s", name, exc)
        return

    store = load_json(INSIGHTS_FILE, {})
    store.setdefault(name, [])
    store[name].append(insight_payload)
    save_json(INSIGHTS_FILE, store)

    # A confirmed competitor should never re-trigger "new competitor" again.
    known_names = set(load_json(KNOWN_NAMES_FILE, []))
    known_names.add(normalize_candidate_name(name))
    save_json(KNOWN_NAMES_FILE, sorted(known_names))

    log.info("Stored insights for '%s'. confidence=%s", name, insight_payload["confidence"])


def query_insights(name: str):
    store = load_json(INSIGHTS_FILE, {})
    records = store.get(name)
    if not records:
        print(f"No stored insights for '{name}' yet.")
        return
    print(json.dumps(records, indent=2))


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="CompetitiveIntelligenceMonitorAgent")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("run", help="Start the twice-daily discovery loop (default).")

    confirm_parser = subparsers.add_parser(
        "confirm", help="Confirm a competitor and trigger deep research."
    )
    confirm_parser.add_argument("--name", required=True, help="Competitor name to confirm.")

    query_parser = subparsers.add_parser(
        "query", help="Print stored insights for a confirmed competitor."
    )
    query_parser.add_argument("--name", required=True, help="Competitor name to look up.")

    args = parser.parse_args()

    if args.command == "confirm":
        confirm_competitor(args.name)
    elif args.command == "query":
        query_insights(args.name)
    else:
        run_loop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped by operator.")
        sys.exit(0)

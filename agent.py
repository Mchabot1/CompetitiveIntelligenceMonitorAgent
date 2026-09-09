"""
CompetitiveIntelligenceMonitorAgent
------------------------------------
Periodically searches public news sources for potential competitors,
surfaces them to Momentum Feed for operator review, and — once an
operator confirms a competitor — performs deeper research and stores
the resulting insights locally for future querying.

This agent is INFORMATIONAL ONLY:
  - It never takes an irreversible action.
  - It only ever calls fe.emit_event(...).
  - It never calls request_approval / requestApproval.

Usage:
    python agent.py run                                  # start the polling loop
    python agent.py confirm --name "Acme Corp"           # operator confirms a competitor
    python agent.py query --name "Acme Corp"             # print stored insights
"""

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import feedparser

from forceequals import ForceEquals, GovernanceBlockedError

# ---------------------------------------------------------------------------
# ForceEquals SDK setup (required snippet — do not modify the shape of this)
# ---------------------------------------------------------------------------

fe = ForceEquals(
    api_key=os.getenv("FORCEEQUALS_API_KEY"),
    agent_id=os.getenv("FORCEEQUALS_AGENT_ID", "competitive-intelligence-monitor-agent"),
)


@fe.governed
def handle_one_case(payload: dict) -> dict:
    fe.emit_event("competitor.potential.detected", payload)
    # Informational: this event appears on Momentum Feed to read.
    # Do NOT call request_approval — nothing irreversible happens.
    return {"ok": True}


@fe.governed
def handle_confirmed_case(payload: dict) -> dict:
    fe.emit_event("competitor.insight", payload)
    # Informational: this event appears on Momentum Feed to read.
    # Do NOT call request_approval — nothing irreversible happens.
    return {"ok": True}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE = os.path.join(DATA_DIR, "seen_items.json")
INSIGHTS_FILE = os.path.join(DATA_DIR, "insights_store.json")

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "600"))  # 10 minutes

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


# ---------------------------------------------------------------------------
# Connector: public news search (no API key required)
# ---------------------------------------------------------------------------

def fetch_potential_competitors(search_terms):
    """
    Polls a public Google News RSS search feed for each search term.
    Returns a list of candidate dicts matching the competitor.potential.detected schema.
    No connector credentials are required for this source.
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
# Main polling loop
# ---------------------------------------------------------------------------

def run_loop():
    log.info(
        "Starting CompetitiveIntelligenceMonitorAgent. Polling every %s seconds for terms: %s",
        POLL_INTERVAL_SECONDS,
        SEARCH_TERMS,
    )
    seen_ids = set(load_json(SEEN_FILE, []))

    while True:
        try:
            candidates = fetch_potential_competitors(SEARCH_TERMS)
        except Exception as exc:
            log.error("Error during discovery: %s", exc)
            candidates = []

        new_count = 0
        for item in candidates:
            item_id = item["id"]
            if item_id in seen_ids:
                continue

            payload = {
                "name": item["name"],
                "source": item["source"],
                "mention_url": item["mention_url"],
                "date_found": item["date_found"],
                "summary": item["summary"],
            }

            try:
                handle_one_case(payload)
                new_count += 1
            except GovernanceBlockedError as exc:
                log.warning("Event blocked by governance for item %s: %s", item_id, exc)
                # Stop processing this case, keep looping.
                continue
            except Exception as exc:
                log.error("Unexpected error handling item %s: %s", item_id, exc)
                continue

            seen_ids.add(item_id)

        if new_count:
            save_json(SEEN_FILE, sorted(seen_ids))
            log.info("Surfaced %s new potential competitor(s) to Momentum Feed.", new_count)
        else:
            log.info("No new potential competitors found this cycle.")

        time.sleep(POLL_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Operator-triggered actions (confirm / deep research / query)
# ---------------------------------------------------------------------------

def confirm_competitor(name: str):
    """
    Called by the operator after reviewing a competitor.potential.detected
    card on Momentum Feed and deciding it is a real competitor. Performs
    deeper research and emits a competitor.insight event, then stores the
    result locally.
    """
    log.info("Operator confirmed competitor: %s. Running deep research...", name)
    insight_payload = fetch_deep_research(name)

    try:
        handle_confirmed_case(insight_payload)
    except GovernanceBlockedError as exc:
        log.warning("Insight event blocked by governance for '%s': %s", name, exc)
        return

    store = load_json(INSIGHTS_FILE, {})
    store.setdefault(name, [])
    store[name].append(insight_payload)
    save_json(INSIGHTS_FILE, store)
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

    subparsers.add_parser("run", help="Start the periodic discovery loop (default).")

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

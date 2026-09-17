"""Slack delivery shared by the event digests.

Two webhook flavours, auto-detected from the URL:

* **Classic incoming webhook** (`hooks.slack.com/services/...`) accepts Block
  Kit, so the digest gets a header and titles as clickable link text.
* **Workflow Builder trigger** (anything else) accepts only the flat data
  variables the workflow declares, substituted as *literal text* -- mrkdwn is
  not interpreted there. So that path sends plain text with bare URLs, and the
  bold headline is applied by the workflow's own message step, which is why
  the headline travels as its own variable.

Callers build a (headline, lines, footer) triple and let this module decide how
to render it.
"""

from __future__ import annotations

import json
import sys
import urllib.request

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) boston-events-feed/1.0"

# Slack limits: 3000 chars per section block, 50 blocks per message, and 4000
# chars for a whole message. The workflow path has no blocks to spread across,
# so it gets the message cap minus room for the trim notice.
SECTION_LIMIT = 2900
WORKFLOW_TEXT_LIMIT = 3900


def log(message: str) -> None:
    print(message, file=sys.stderr)


def slack_escape(text: str) -> str:
    """Escape for mrkdwn. Not used on the workflow path, where `&amp;` would
    show up as itself."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def is_workflow_webhook(webhook: str) -> bool:
    """Tell a Workflow Builder trigger from a classic incoming webhook.

    Classic incoming webhooks are always hooks.slack.com/services/...
    Workflow Builder has used several shapes over the years (/triggers/,
    /workflows/, slack.com/shortcuts/...), so treat anything that isn't
    /services/ as a workflow trigger.
    """
    return bool(webhook) and "/services/" not in webhook


def pack_lines(lines: list[str], limit: int = SECTION_LIMIT) -> list[str]:
    """Group lines into chunks that each stay under Slack's per-section limit."""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in lines:
        # +1 for the joining newline.
        if current and size + len(line) + 1 > limit:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def build_block_payload(headline: str, lines: list[str], footer: str) -> dict:
    """Block Kit payload for a classic incoming webhook."""
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{headline}*"}}]
    for chunk in pack_lines(lines):
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})
    blocks.append(
        {"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]}
    )
    return {"text": headline, "blocks": blocks}


def build_workflow_payload(
    headline: str,
    lines: list[str],
    footer: str,
    overflow_footer: str | None = None,
) -> dict:
    """Two flat variables for a Workflow Builder trigger.

    `headline` is separate so the workflow's message step can bold it -- Slack
    applies the step's rich-text styling to whatever a variable resolves to,
    which is the only route to real formatting on this path.

    If the body would exceed the message cap, entries are dropped from the tail
    (callers should sort the most droppable last) and `overflow_footer` is used
    instead, formatted with the number dropped.
    """

    def assemble(shown: list[str], dropped: int) -> str:
        if dropped and overflow_footer:
            tail = overflow_footer.format(dropped=dropped)
        elif dropped:
            tail = f"+{dropped} more — {footer}"
        else:
            tail = footer
        return "\n".join([*shown, "", tail])

    body = assemble(lines, 0)
    dropped = 0
    while len(headline) + len(body) > WORKFLOW_TEXT_LIMIT and len(lines) - dropped > 1:
        dropped += 1
        body = assemble(lines[: len(lines) - dropped], dropped)
    if dropped:
        log(f"warning: trimmed {dropped} entries to fit Slack's message limit")
    return {"headline": headline, "text": body}


def build_payload(
    headline: str,
    lines: list[str],
    footer: str,
    workflow_mode: bool,
    plain_footer: str | None = None,
    overflow_footer: str | None = None,
) -> dict:
    """Render for whichever webhook flavour is in play."""
    if workflow_mode:
        return build_workflow_payload(
            headline, lines, plain_footer or footer, overflow_footer
        )
    return build_block_payload(headline, lines, footer)


def slack_response_ok(body: str) -> bool:
    """Classic incoming webhooks reply with the literal string "ok";
    Workflow Builder triggers reply with JSON containing "ok": true."""
    if body.strip() == "ok":
        return True
    try:
        return json.loads(body).get("ok") is True
    except (json.JSONDecodeError, AttributeError):
        return False


def post_to_slack(webhook: str, payload: dict) -> None:
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode(errors="replace").strip()
        if resp.status != 200 or not slack_response_ok(body):
            raise SystemExit(f"Slack rejected the post: HTTP {resp.status} {body}")


def render_preview(payload: dict) -> str:
    """Approximate how Slack will show it, for --dry-run."""
    if "blocks" not in payload:
        return f"[bold] {payload['headline']}\n{payload['text']}"
    out = []
    for block in payload["blocks"]:
        if block["type"] == "section":
            out.append(block["text"]["text"])
        elif block["type"] == "context":
            out.append(block["elements"][0]["text"])
    return "\n".join(out)

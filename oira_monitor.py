import os
import re
import json
import smtplib
import logging
import sys
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

URL = "https://www.reginfo.gov/public/do/eAgendaViewRule?pubId=202504&RIN=3060-AM01"
STATE_FILE = "status_state.json"

GMAIL_ADDRESS = "sydneyslossberg1212@gmail.com"
NOTIFY_EMAIL = "investments@woodycreekcp.com"

# Specific labels to extract from the reginfo.gov page (label text -> key name)
TARGET_LABELS = {
    "RIN Status":                   "rin_status",
    "Agenda Stage of Rulemaking":   "agenda_stage",
    "Priority":                     "priority",
    "Major":                        "major",
    "Unfunded Mandates":            "unfunded_mandates",
    "EO 14192 Designation":         "eo_14192_designation",
    "Included in the Regulatory Plan": "in_reg_plan",
}


def fetch_page(url: str) -> str:
    log.info(f"Fetching {url}")
    resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    log.info(f"Received {len(resp.text)} bytes")
    return resp.text


def parse_fields(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    full_text = soup.get_text(separator="\n")
    data = {}

    for label, key in TARGET_LABELS.items():
        # Look for "Label: Value" anywhere in the page text
        pattern = rf"{re.escape(label)}\s*:\s*(.+)"
        match = re.search(pattern, full_text)
        if match:
            value = match.group(1).strip()
            # Trim at the next newline or next label-like pattern
            value = value.split("\n")[0].strip()
            data[key] = value

    # Extract next timetable action separately
    next_action_match = re.search(
        r"Next Action\s+Undetermined\s+([\w\s,]+?)(?:\n|$)", full_text
    )
    if next_action_match:
        data["next_action"] = next_action_match.group(1).strip()
    else:
        # Simpler fallback
        match = re.search(r"Next Action[^\n]*\n\s*(.+)", full_text)
        if match:
            data["next_action"] = match.group(1).strip()

    log.info(f"Parsed fields: {json.dumps(data, indent=2)}")
    return data


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        log.info(f"Loaded state from {STATE_FILE}")
    else:
        state = {"last_status": None, "last_checked": None, "history": []}
        log.info("No existing state file; starting fresh")
    return state


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    log.info(f"State saved to {STATE_FILE}")


def send_email(subject: str, body: str) -> None:
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if not gmail_password:
        log.error("GMAIL_APP_PASSWORD env var is not set — skipping email")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = NOTIFY_EMAIL
    msg.attach(MIMEText(body, "plain"))

    log.info(f"Connecting to smtp.gmail.com:465 ...")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, gmail_password)
            server.sendmail(GMAIL_ADDRESS, NOTIFY_EMAIL, msg.as_string())
        log.info(f"Email sent to {NOTIFY_EMAIL}")
    except smtplib.SMTPAuthenticationError as e:
        log.error(f"Gmail authentication failed — check GMAIL_APP_PASSWORD: {e}")
    except Exception as e:
        log.error(f"Failed to send email: {e}")


def build_change_summary(old: dict | None, new: dict) -> str:
    lines = [
        "OIRA Rule Status Change Detected",
        "=" * 40,
        f"RIN: 3060-AM01 — Promoting PNT Technologies and Solutions",
        f"URL: {URL}",
        "",
    ]
    if old is None:
        lines.append("First-time check — baseline state recorded:")
        for k, v in new.items():
            lines.append(f"  {k}: {v}")
    else:
        lines.append("Changed fields:")
        for key in TARGET_LABELS.values():
            old_val = old.get(key, "(not present)")
            new_val = new.get(key, "(not present)")
            if old_val != new_val:
                lines.append(f"  {key}:")
                lines.append(f"    Before: {old_val}")
                lines.append(f"    After:  {new_val}")
        lines.append("")
        lines.append("Full current state:")
        for k, v in new.items():
            lines.append(f"  {k}: {v}")
    lines.append(f"\nChecked at: {datetime.now(timezone.utc).isoformat()}")
    return "\n".join(lines)


def main() -> None:
    html = fetch_page(URL)
    current = parse_fields(html)
    now = datetime.now(timezone.utc).isoformat()

    state = load_state()
    previous = state.get("last_status")

    # On first run, save baseline without sending an alert
    if previous is None:
        log.info("First run — saving baseline state, no alert sent")
        changed = False
    else:
        changed = current != previous

    if changed:
        log.info("Change detected — sending alert")
        subject = "OIRA Status Change: RIN 3060-AM01 (PNT)"
        body = build_change_summary(previous, current)
        send_email(subject, body)
    else:
        log.info("No change detected")

    state["last_status"] = current
    state["last_checked"] = now
    state.setdefault("history", []).append({
        "checked_at": now,
        "changed": changed,
        "state": current,
    })

    save_state(state)


if __name__ == "__main__":
    main()

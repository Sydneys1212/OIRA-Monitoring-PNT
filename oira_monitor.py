import os
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

FIELDS_OF_INTEREST = ["status", "stage", "action", "rin", "title", "agency"]


def fetch_page(url: str) -> str:
    log.info(f"Fetching {url}")
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    log.info(f"Received {len(resp.text)} bytes")
    return resp.text


def parse_fields(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    data = {}

    # reginfo.gov renders a table with label/value pairs; collect all th/td pairs
    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) >= 2:
            label = cells[0].get_text(separator=" ", strip=True).lower()
            value = cells[1].get_text(separator=" ", strip=True)
            for field in FIELDS_OF_INTEREST:
                if field in label:
                    data[field] = value

    # Also try definition-list style (dt/dd)
    for dt in soup.find_all("dt"):
        label = dt.get_text(separator=" ", strip=True).lower()
        dd = dt.find_next_sibling("dd")
        if dd:
            value = dd.get_text(separator=" ", strip=True)
            for field in FIELDS_OF_INTEREST:
                if field in label:
                    data[field] = value

    # Fallback: look for labelled spans / divs
    for tag in soup.find_all(["span", "div", "p"]):
        label = tag.get_text(separator=" ", strip=True).lower()
        for field in FIELDS_OF_INTEREST:
            if label.startswith(field + ":") or label.startswith(field + " :"):
                value = label.split(":", 1)[1].strip()
                if value:
                    data.setdefault(field, value)

    log.info(f"Parsed fields: {data}")
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


GMAIL_ADDRESS = "sydneyslossberg1212@gmail.com"
NOTIFY_EMAIL = "investments@woodycreekcp.com"


def send_email(subject: str, body: str) -> None:
    gmail_address = GMAIL_ADDRESS
    gmail_password = os.environ["GMAIL_APP_PASSWORD"]
    notify_email = NOTIFY_EMAIL

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_address
    msg["To"] = notify_email
    msg.attach(MIMEText(body, "plain"))

    log.info(f"Sending email to {notify_email} via SSL on port 465")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_address, gmail_password)
        server.sendmail(gmail_address, notify_email, msg.as_string())
    log.info("Email sent successfully")


def build_change_summary(old: dict | None, new: dict) -> str:
    lines = ["OIRA Rule Status Change Detected", "=" * 40, f"URL: {URL}", ""]
    if old is None:
        lines.append("First-time check — current state recorded:")
        for k, v in new.items():
            lines.append(f"  {k}: {v}")
    else:
        lines.append("Changed fields:")
        for field in FIELDS_OF_INTEREST:
            old_val = (old or {}).get(field, "(not present)")
            new_val = new.get(field, "(not present)")
            if old_val != new_val:
                lines.append(f"  {field}:")
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

    changed = current != previous

    if changed:
        log.info("Change detected — preparing alert")
        subject = "OIRA Rule Status Change: RIN 3060-AM01"
        body = build_change_summary(previous, current)
        try:
            send_email(subject, body)
        except Exception as exc:
            log.error(f"Failed to send email: {exc}")
    else:
        log.info("No change detected")

    history_entry = {
        "checked_at": now,
        "changed": changed,
        "state": current,
    }
    state["last_status"] = current
    state["last_checked"] = now
    state.setdefault("history", []).append(history_entry)

    save_state(state)


if __name__ == "__main__":
    main()

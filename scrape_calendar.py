#!/usr/bin/env python3
"""Build one minimal potential-work ICS feed from three public venue calendars."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


TZ = ZoneInfo("America/New_York")
TODAY = datetime.now(TZ).date()
OUTPUT = Path(__file__).with_name("potential-work.ics")
STATUS_OUTPUT = Path(__file__).with_name("last-run.txt")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; PotentialWorkCalendar/1.0; "
        "+https://github.com/NukaHusky/potential-work-calendar)"
    )
}


@dataclass(frozen=True)
class WorkEvent:
    venue: str
    description: str
    start: datetime
    source_url: str
    source_key: str

    @property
    def end(self) -> datetime:
        return self.start + timedelta(hours=5)

    @property
    def uid(self) -> str:
        raw = f"{self.venue}|{self.source_key}".encode()
        return f"{hashlib.sha256(raw).hexdigest()[:32]}@potential-work-calendar"


def fetch(url: str, attempts: int = 3) -> str:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = requests.get(url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise RuntimeError(f"Could not fetch {url}: {last_error}")


def clean(value: str) -> str:
    return " ".join(html.unescape(value).split())


def parse_clock(value: str) -> clock_time:
    normalized = clean(value).replace(".", "").upper().replace(" ", "")
    return datetime.strptime(normalized, "%I:%M%p").time()


def parse_date_range(value: str) -> list[date]:
    value = clean(value)
    matches = re.findall(r"[A-Z][a-z]+\s+\d{1,2},\s+\d{4}", value)
    if not matches:
        return []
    start = datetime.strptime(matches[0], "%B %d, %Y").date()
    end = datetime.strptime(matches[-1], "%B %d, %Y").date()
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def detail_rows(soup: BeautifulSoup) -> dict[str, str]:
    rows: dict[str, str] = {}
    for row in soup.select(".detail_row"):
        left = row.select_one(".row_left")
        right = row.select_one(".row_right")
        if left and right:
            rows[clean(left.get_text(" "))] = clean(right.get_text(" "))
    return rows


def cabot_events() -> list[WorkEvent]:
    base = "https://thecabot.org"
    detail_urls: list[str] = []
    seen: set[str] = set()

    for page_number in range(1, 20):
        suffix = "" if page_number == 1 else f"page/{page_number}/"
        url = f"{base}/whats-on/{suffix}?instance_dates=&location=The+Cabot&genre="
        soup = BeautifulSoup(fetch(url), "html.parser")
        page_urls = []
        for card in soup.select(".event_item"):
            link = card.select_one('a[href*="/event/"]')
            if link:
                event_url = urljoin(base, link.get("href", ""))
                if event_url not in seen:
                    seen.add(event_url)
                    page_urls.append(event_url)
        if not page_urls:
            break
        detail_urls.extend(page_urls)
        if not soup.select_one("a.next.page-numbers"):
            break

    events: list[WorkEvent] = []
    for event_url in detail_urls:
        soup = BeautifulSoup(fetch(event_url), "html.parser")
        rows = detail_rows(soup)
        location = ""
        event_name = ""
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.string or "null")
            except json.JSONDecodeError:
                continue
            candidates = data if isinstance(data, list) else [data]
            for item in candidates:
                if isinstance(item, dict) and item.get("@type") == "Event":
                    event_name = clean(str(item.get("name", "")))
                    place = item.get("location", {})
                    if isinstance(place, dict):
                        location = clean(str(place.get("name", "")))
        if location and location.casefold() != "the cabot":
            continue
        if not event_name:
            heading = soup.select_one("h1, .event_title h1, .event_title .h1")
            event_name = clean(heading.get_text(" ") if heading else "")

        dates = parse_date_range(rows.get("Date", ""))
        show_times = [parse_clock(v) for v in rows.get("Show Times", "").split("|") if clean(v)]
        door_times = [parse_clock(v) for v in rows.get("Door Times", "").split("|") if clean(v)]
        if not event_name:
            print(f"Skipping Cabot listing without a usable date/time: {event_url}", file=sys.stderr)
            continue

        instances: list[tuple[date, clock_time, int]] = []
        # Ticket rows are authoritative for multi-day and multi-show events.
        for index, row in enumerate(soup.select(".show_details_table .show_row")):
            columns = row.select(".show_col")
            date_node = row.select_one(".date_col")
            if not date_node or len(columns) < 2:
                continue
            parsed_dates = parse_date_range(clean(date_node.get_text(" ")))
            if not parsed_dates:
                continue
            try:
                show_at = parse_clock(clean(columns[1].get_text(" ")))
            except ValueError:
                continue
            instances.append((parsed_dates[0], show_at, index))

        if instances:
            pass
        elif len(dates) > 1 and len(show_times) == 1:
            instances = [(day, show_times[0], i) for i, day in enumerate(dates)]
        elif len(dates) == 1:
            instances = [(dates[0], show, i) for i, show in enumerate(show_times)]
        elif len(dates) == len(show_times):
            instances = [(day, show, i) for i, (day, show) in enumerate(zip(dates, show_times))]
        else:
            print(f"Skipping ambiguous Cabot date/time combination: {event_url}", file=sys.stderr)
            continue

        for day, show_at, index in instances:
            if day < TODAY:
                continue
            if len(door_times) == len(instances):
                door_at = door_times[min(index, len(door_times) - 1)]
                start = datetime.combine(day, door_at, TZ) - timedelta(hours=1)
            elif len(door_times) == 1 and len(instances) == 1:
                start = datetime.combine(day, door_times[0], TZ) - timedelta(hours=1)
            else:
                start = datetime.combine(day, show_at, TZ) - timedelta(hours=2)
            events.append(
                WorkEvent(
                    venue="THE CABOT",
                    description=event_name,
                    start=start,
                    source_url=event_url,
                    source_key=f"{event_url}|{day.isoformat()}|{index}",
                )
            )
    return events


def chevalier_events() -> list[WorkEvent]:
    url = "https://chevaliertheatre.com/calendar/"
    soup = BeautifulSoup(fetch(url), "html.parser")
    allowed_ids = {
        clean(node.get("data-event-id", ""))
        for node in soup.select('.event-list .event-item[data-venue-id="2"] .event-title[data-event-id]')
    }
    events: list[WorkEvent] = []
    for modal in soup.select(".event-modal"):
        modal_id = modal.get("id", "").removeprefix("event-modal-")
        if modal_id not in allowed_ids:
            continue
        name_node = modal.select_one(".modal-event-name")
        info_link = modal.select_one('a.modal-show-info[href*="/event/"]')
        if not name_node or not info_link:
            continue
        event_name = clean(name_node.get_text(" "))
        event_url = urljoin(url, info_link.get("href", ""))
        for index, row in enumerate(modal.select(".modal-ticket-row")):
            when = row.select_one(".modal-ticket-when")
            if not when:
                continue
            try:
                show = datetime.strptime(clean(when.get_text(" ")), "%b %d, %Y at %I:%M %p").replace(tzinfo=TZ)
            except ValueError:
                print(f"Skipping unrecognized Chevalier time: {clean(when.get_text(' '))}", file=sys.stderr)
                continue
            if show.date() < TODAY:
                continue
            events.append(
                WorkEvent(
                    venue="CHEVALIER THEATRE",
                    description=event_name,
                    start=show - timedelta(hours=2),
                    source_url=event_url,
                    source_key=f"{event_url}|{show.date().isoformat()}|{index}",
                )
            )
    return events


def next_date(month: int, day: int) -> date:
    candidate = date(TODAY.year, month, day)
    return candidate if candidate >= TODAY else date(TODAY.year + 1, month, day)


def deep_cuts_events() -> list[WorkEvent]:
    url = "https://www.deepcuts.rocks/events"
    events: list[WorkEvent] = []
    month_numbers = {
        "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
    }

    snapshot_path = os.environ.get("DEEP_CUTS_SNAPSHOT_FILE")
    if snapshot_path:
        snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
        for index, item in enumerate(snapshot):
            instant = datetime.fromisoformat(str(item["startDate"]).replace("Z", "+00:00")).astimezone(TZ)
            if instant.date() >= TODAY:
                event_url = clean(str(item.get("url", url)))
                events.append(
                    WorkEvent(
                        venue="DEEP CUTS",
                        description=clean(str(item.get("name", "Deep Cuts event"))),
                        start=datetime.combine(instant.date(), clock_time(18, 0), TZ),
                        source_url=event_url,
                        source_key=f"{event_url}|{instant.date().isoformat()}|{index}",
                    )
                )
        return events

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(user_agent=HEADERS["User-Agent"])
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        frame = page.frame_locator("iframe")
        frame.locator("article").first.wait_for(state="visible", timeout=30_000)

        for _ in range(30):
            load_more = frame.get_by_text("LOAD MORE", exact=True)
            try:
                if load_more.count() == 0 or not load_more.first.is_visible():
                    break
                load_more.first.click()
                page.wait_for_timeout(750)
            except PlaywrightTimeoutError:
                break

        for index, raw in enumerate(frame.locator('script[type="application/ld+json"]').all_text_contents()):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for item in data if isinstance(data, list) else [data]:
                if not isinstance(item, dict) or not item.get("startDate"):
                    continue
                instant = datetime.fromisoformat(str(item["startDate"]).replace("Z", "+00:00")).astimezone(TZ)
                if instant.date() < TODAY:
                    continue
                event_url = clean(str(item.get("url", url)))
                events.append(
                    WorkEvent(
                        venue="DEEP CUTS",
                        description=clean(str(item.get("name", "Deep Cuts event"))),
                        start=datetime.combine(instant.date(), clock_time(18, 0), TZ),
                        source_url=event_url,
                        source_key=f"{event_url}|{instant.date().isoformat()}|{index}",
                    )
                )

        for card in page.locator("div.w-container.row").all():
            text = clean(card.inner_text())
            if "BUY TICKETS" not in text.upper():
                continue
            match = re.search(
                r"^(?P<name>.+?)\s+(?P<month>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+"
                r"(?P<day>\d{1,2})(?:ST|ND|RD|TH)\b",
                text,
                re.IGNORECASE,
            )
            if not match:
                continue
            event_day = next_date(month_numbers[match.group("month").upper()], int(match.group("day")))
            link = card.locator('a[href*="ticketmaster.com/event"]').first
            event_url = link.get_attribute("href") or url
            events.append(
                WorkEvent(
                    venue="DEEP CUTS",
                    description=clean(match.group("name")).title(),
                    start=datetime.combine(event_day, clock_time(18, 0), TZ),
                    source_url=event_url,
                    source_key=f"partner|{event_url}|{event_day.isoformat()}",
                )
            )
        browser.close()
    return events


def ics_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def utc_stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def fold_ics_line(line: str, limit: int = 73) -> list[str]:
    if len(line) <= limit:
        return [line]
    result = [line[:limit]]
    line = line[limit:]
    while line:
        result.append(" " + line[: limit - 1])
        line = line[limit - 1 :]
    return result


def unique_sorted(events: Iterable[WorkEvent]) -> list[WorkEvent]:
    unique: dict[tuple[str, str, datetime], WorkEvent] = {}
    for event in events:
        unique[(event.venue, event.description.casefold(), event.start)] = event
    return sorted(unique.values(), key=lambda event: (event.start, event.venue, event.description))


def render_ics(events: Iterable[WorkEvent]) -> str:
    generated = datetime.now(timezone.utc)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Potential Work Calendar//NukaHusky//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Potential Work Events",
        "X-WR-TIMEZONE:America/New_York",
        "REFRESH-INTERVAL;VALUE=DURATION:P1D",
        "X-PUBLISHED-TTL:P1D",
    ]
    for event in unique_sorted(events):
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{event.uid}",
                f"DTSTAMP:{utc_stamp(generated)}",
                f"DTSTART:{utc_stamp(event.start)}",
                f"DTEND:{utc_stamp(event.end)}",
                f"SUMMARY:{ics_escape(event.venue)}",
                f"DESCRIPTION:{ics_escape(event.description)}",
                "STATUS:TENTATIVE",
                "TRANSP:OPAQUE",
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    return "\r\n".join(part for line in lines for part in fold_ics_line(line)) + "\r\n"


def main() -> None:
    all_events: list[WorkEvent] = []
    failures: list[str] = []
    for name, collector in (
        ("The Cabot", cabot_events),
        ("Deep Cuts", deep_cuts_events),
        ("Chevalier Theatre", chevalier_events),
    ):
        try:
            collected = collector()
            if not collected:
                raise RuntimeError("collector returned no future events")
            all_events.extend(collected)
            print(f"{name}: {len(collected)} events")
        except Exception as exc:
            failures.append(f"{name}: {exc}")

    if failures:
        raise RuntimeError("Calendar not replaced because collection failed:\n" + "\n".join(failures))

    events = unique_sorted(event for event in all_events if event.start.date() >= TODAY)
    OUTPUT.write_text(render_ics(events), encoding="utf-8")
    counts = {venue: sum(event.venue == venue for event in events) for venue in sorted({e.venue for e in events})}
    STATUS_OUTPUT.write_text(
        f"Last successful refresh: {datetime.now(TZ).isoformat(timespec='seconds')}\n"
        + "\n".join(f"{venue}: {count}" for venue, count in counts.items())
        + f"\nTotal: {len(events)}\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(events)} events to {OUTPUT}")


if __name__ == "__main__":
    main()

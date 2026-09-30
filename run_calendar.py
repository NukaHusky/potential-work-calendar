#!/usr/bin/env python3
"""Apply a small runtime compatibility patch, then build the calendar.

Deep Cuts occasionally renders event cards whose ticket link is missing or
changes while Playwright is inspecting it. The main scraper used to wait 30
seconds for that missing link and fail the entire daily refresh, leaving the
published ICS stale. This wrapper makes that optional link non-fatal.
"""

from pathlib import Path
import runpy

scraper = Path(__file__).with_name("scrape_calendar.py")
text = scraper.read_text(encoding="utf-8")

old = '''            link = card.locator('a[href*="ticketmaster.com/event"]').first
            event_url = link.get_attribute("href") or url
'''
new = '''            links = card.locator('a[href*="ticketmaster.com/event"]')
            if links.count():
                try:
                    event_url = links.first.get_attribute("href", timeout=2_000) or url
                except PlaywrightTimeoutError:
                    event_url = url
            else:
                event_url = url
'''

if old not in text:
    raise RuntimeError("Expected Deep Cuts ticket-link code was not found; scraper changed")

scraper.write_text(text.replace(old, new, 1), encoding="utf-8")
runpy.run_path(str(scraper), run_name="__main__")

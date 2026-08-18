from datetime import datetime

from scrape_calendar import TZ, WorkEvent, render_ics, unique_sorted


def test_minimal_event_shape_and_duration():
    event = WorkEvent(
        venue="DEEP CUTS",
        description="Califone / Early Worm",
        start=datetime(2026, 8, 22, 18, 0, tzinfo=TZ),
        source_url="https://example.com/event",
        source_key="deep-cuts-example",
    )
    output = render_ics([event])
    assert "SUMMARY:DEEP CUTS" in output
    assert "DESCRIPTION:Califone / Early Worm" in output
    assert "DTSTART:20260822T220000Z" in output
    assert "DTEND:20260823T030000Z" in output
    assert "STATUS:TENTATIVE" in output


def test_duplicate_potential_blocks_are_removed():
    event = WorkEvent(
        venue="DEEP CUTS",
        description="Partner Event",
        start=datetime(2026, 10, 11, 18, 0, tzinfo=TZ),
        source_url="https://example.com/one",
        source_key="one",
    )
    assert unique_sorted([event, event]) == [event]

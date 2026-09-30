from datetime import datetime

from scrape_calendar import (
    TZ,
    WorkEvent,
    parse_chevalier_ticket_date,
    parse_optional_clock,
    render_ics,
    unique_sorted,
)


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


def test_relative_cabot_door_time_uses_fallback():
    assert parse_optional_clock("1 HR PRIOR TO SHOW") is None
    assert parse_optional_clock("7:00 PM").hour == 19


def test_chevalier_ticket_date_accepts_explicit_year():
    reference = datetime(2026, 9, 25).date()
    assert parse_chevalier_ticket_date("September 25, 2026", reference) == reference


def test_chevalier_ticket_date_accepts_legacy_month_day():
    reference = datetime(2026, 12, 30).date()
    assert parse_chevalier_ticket_date("December 30", reference) == reference


import pytest
import scrape_calendar as calendar
from datetime import date, timedelta


@pytest.mark.parametrize('ticket', [
    '<a href="https://www.ticketmaster.com/event/123">BUY TICKETS</a>',
    '<a href="https://www.eventbrite.com/e/123">BUY TICKETS</a>',
    '<button>BUY TICKETS</button>',
])
def test_partner_cards_accept_optional_ticket_links_without_duplicate_wrapper(monkeypatch, ticket):
    monkeypatch.setattr(calendar, 'TODAY', date(2026, 9, 30))
    markup = f'''<div class="w-container row"><h3>PARTNER EVENTS</h3>
        <div class="w-container row"><h3>EMO yoga</h3><p>OCT 10TH | 7PM</p>{ticket}</div>
        </div>'''
    events = calendar.deep_cuts_partner_events(markup, 'https://www.deepcuts.rocks/events')
    assert len(events) == 1
    assert events[0].description == 'Emo Yoga'
    assert events[0].start == datetime(2026, 10, 10, 18, tzinfo=TZ)
    expected_url = ('https://www.ticketmaster.com/event/123' if 'ticketmaster' in ticket
                    else 'https://www.eventbrite.com/e/123' if 'eventbrite' in ticket
                    else 'https://www.deepcuts.rocks/events')
    assert events[0].source_url == expected_url


def test_missing_links_do_not_collapse_distinct_shows(monkeypatch):
    monkeypatch.setattr(calendar, 'TODAY', date(2026, 9, 30))
    markup = ''.join(f'<div class="w-container row">{name} OCT 10TH | 7PM <button>BUY TICKETS</button></div>'
                     for name in ('Show A', 'Show B'))
    events = calendar.deep_cuts_partner_events(markup, 'https://www.deepcuts.rocks/events')
    assert len({event.uid for event in events}) == 2


def test_unrecognized_partner_date_fails_instead_of_silently_omitting():
    with pytest.raises(RuntimeError, match='Unrecognized Deep Cuts partner card'):
        calendar.deep_cuts_partner_events(
            '<div class="w-container row">Show SOMEDAY <button>BUY TICKETS</button></div>',
            'https://www.deepcuts.rocks/events')


@pytest.mark.parametrize('failure', ['exception', 'empty'])
def test_failed_collection_preserves_both_published_files(monkeypatch, tmp_path, failure):
    output, status = tmp_path / 'calendar.ics', tmp_path / 'last-run.txt'
    output.write_text('previous calendar')
    status.write_text('previous status')
    monkeypatch.setattr(calendar, 'OUTPUT', output)
    monkeypatch.setattr(calendar, 'STATUS_OUTPUT', status)
    event = WorkEvent('THE CABOT', 'Show', datetime.combine(calendar.TODAY, datetime.min.time(), TZ), 'url', 'key')
    monkeypatch.setattr(calendar, 'cabot_events', lambda: [event])
    monkeypatch.setattr(calendar, 'chevalier_events', lambda: [event])
    def broken():
        if failure == 'exception':
            raise RuntimeError('widget failed')
        return []
    monkeypatch.setattr(calendar, 'deep_cuts_events', broken)
    with pytest.raises(RuntimeError, match='Calendar not replaced'):
        calendar.main()
    assert output.read_text() == 'previous calendar'
    assert status.read_text() == 'previous status'


def test_success_replaces_feed_and_removes_past_events(monkeypatch, tmp_path):
    output, status = tmp_path / 'calendar.ics', tmp_path / 'last-run.txt'
    output.write_text('stale feed')
    monkeypatch.setattr(calendar, 'OUTPUT', output)
    monkeypatch.setattr(calendar, 'STATUS_OUTPUT', status)
    for collector, venue in [('cabot_events', 'THE CABOT'), ('deep_cuts_events', 'DEEP CUTS'),
                             ('chevalier_events', 'CHEVALIER THEATRE')]:
        events = [WorkEvent(venue, name, datetime.combine(calendar.TODAY + timedelta(days=offset),
                  datetime.strptime('19:00', '%H:%M').time(), TZ), 'url', name) for name, offset in [('old show', -1), ('future show', 1)]]
        monkeypatch.setattr(calendar, collector, lambda events=events: events)
    calendar.main()
    result = output.read_text()
    assert result.count('BEGIN:VEVENT') == 3
    assert 'old show' not in result
    assert 'Total: 3' in status.read_text()


def test_evening_filter_uses_showtime_not_work_start(monkeypatch, tmp_path):
    monkeypatch.setattr(calendar, 'TODAY', date(2026, 9, 30))
    monkeypatch.setattr(calendar, 'OUTPUT', tmp_path / 'calendar.ics')
    monkeypatch.setattr(calendar, 'STATUS_OUTPUT', tmp_path / 'last-run.txt')
    events = [WorkEvent('THE CABOT', label, datetime(2026, 10, 1, 15, tzinfo=TZ), 'url', label,
                       datetime(2026, 10, 1, hour, minute, tzinfo=TZ))
              for label, hour, minute in [('morning', 11, 0), ('matinee', 16, 59), ('evening', 17, 0)]]
    yoga = calendar.deep_cuts_partner_events(
        '<div class="w-container row">Emo Yoga OCT 10TH | 11AM <button>BUY TICKETS</button></div>',
        'https://www.deepcuts.rocks/events')
    monkeypatch.setattr(calendar, 'cabot_events', lambda: events)
    monkeypatch.setattr(calendar, 'chevalier_events', lambda: events)
    monkeypatch.setattr(calendar, 'deep_cuts_events', lambda: yoga)
    calendar.main()
    result = calendar.OUTPUT.read_text()
    assert 'DESCRIPTION:evening' in result
    assert 'DTSTART:20261001T190000Z' in result  # 3 PM work block remains.
    assert 'DESCRIPTION:morning' not in result
    assert 'DESCRIPTION:matinee' not in result
    assert 'Emo Yoga' not in result  # 11 AM show must not become a 6 PM show.
    assert result.count('BEGIN:VEVENT') == 1


def test_partner_without_showtime_fails_safely():
    with pytest.raises(RuntimeError, match='Missing Deep Cuts partner showtime'):
        calendar.deep_cuts_partner_events(
            '<div class="w-container row">Show OCT 10TH <button>BUY TICKETS</button></div>',
            'https://www.deepcuts.rocks/events')


def test_deep_cuts_snapshot_preserves_actual_local_showtime(monkeypatch, tmp_path):
    import json
    snapshot = tmp_path / 'deep.json'
    snapshot.write_text(json.dumps([{'name': 'Afternoon show', 'startDate': '2026-10-01T20:00:00Z'}]))
    monkeypatch.setenv('DEEP_CUTS_SNAPSHOT_FILE', str(snapshot))
    monkeypatch.setattr(calendar, 'TODAY', date(2026, 9, 30))
    event, = calendar.deep_cuts_events()
    assert event.show_start == datetime(2026, 10, 1, 16, tzinfo=TZ)
    assert event.start.hour == 18

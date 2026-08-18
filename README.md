# Potential Work Calendar

This repository builds one read-only calendar of possible work dates from the public event listings for:

- The Cabot
- Deep Cuts
- Chevalier Theatre

The calendar refreshes every morning at 6:00 AM Eastern. It contains only today and future dates. Each event uses the venue as its title, the artist or event as its description, and a five-hour duration.

## Timing rules

- **Deep Cuts:** 6:00 PM start.
- **Chevalier Theatre:** one hour before doors. The venue publishes doors as one hour before showtime, so the calendar starts two hours before showtime.
- **The Cabot:** one hour before the published doors time. If no doors time is published, the calendar starts two hours before showtime.

Events are marked tentative because this calendar is intended to anticipate possible work before employee schedules are released.

## Proton Calendar subscription

Use this URL in Proton Calendar's **Add calendar from URL** screen:

```text
https://raw.githubusercontent.com/NukaHusky/potential-work-calendar/main/potential-work.ics
```

Subscribed calendars are read-only. Proton controls its own refresh timing, so a GitHub update may take several hours to appear.

## Manual refresh

Open the repository's **Actions** tab, select **Update potential work calendar**, and choose **Run workflow**.

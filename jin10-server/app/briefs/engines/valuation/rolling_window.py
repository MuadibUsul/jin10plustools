"""Calendar-year windows; storage history and chart history are independent."""
from datetime import date


def window_bounds(end_date, years=10):
    if type(years) is not int or not 1 <= years <= 100:
        raise ValueError('window_years must be a positive integer')
    end = date.fromisoformat(end_date)
    try:
        start = end.replace(year=end.year-years)
    except ValueError:
        start = end.replace(year=end.year-years, day=28)
    return start.isoformat(), end.isoformat()


def select_window(rows, years=10, end_date=None):
    if not rows:
        raise ValueError('Chart history is empty')
    start, end = window_bounds(end_date or max(r['date'] for r in rows), years)
    # Preserve source order and duplicate-category records where the source has
    # them. A data-specific validator, not the window selector, owns that policy.
    selected = [r for r in rows if start <= r['date'] <= end]
    if not selected:
        raise ValueError('Chart window has no available source records')
    return selected, {'window_years': years, 'calendar_start': start, 'calendar_end': end,
                      'first_data_date': selected[0]['date'], 'last_data_date': selected[-1]['date']}

import calendar
from datetime import date


def _months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month)


def is_due_change(schedule, today: date) -> bool:
    """Return True if the schedule's investment is due on or by today.

    Handles short months (e.g., February): when day_of_month is 29–31 and the month
    has fewer days, we treat the last day of the month as due (e.g., Feb 28 for a
    day-31 schedule).
    """
    if not schedule or not schedule.is_active:
        return False
    if not schedule.start_date or (schedule.change_frequency_months or 0) < 1:
        return False
    months_since = _months_between(schedule.start_date, today)
    if months_since < 0:
        return False
    target_day = schedule.day_of_month or 1
    last_day = calendar.monthrange(today.year, today.month)[1]
    # Due if: past target day, OR on last day of short month where target_day doesn't exist
    day_ok = today.day >= target_day or (today.day == last_day and target_day > last_day)
    return (months_since % schedule.change_frequency_months) == 0 and day_ok

def is_due_withdrawal(schedule, today: date) -> bool:
    """Return True if a withdrawal is due on or by today. Handles short months."""
    if not schedule or not schedule.is_active:
        return False
    mode = (schedule.withdrawal_mode or 'AD_HOC').upper()
    target_day = schedule.day_of_month or 1
    last_day = calendar.monthrange(today.year, today.month)[1]
    day_ok = today.day >= target_day or (today.day == last_day and target_day > last_day)
    if mode == 'AD_HOC':
        return False
    if mode == 'MONTHLY':
        return day_ok
    if mode == 'N_MONTHS':
        freq = max(schedule.withdrawal_frequency_months or 0, 0)
        if freq < 1 or not schedule.start_date:
            return False
        months_since = _months_between(schedule.start_date, today)
        if months_since < 0:
            return False
        return (months_since % freq) == 0 and day_ok
    return False

def validate_amount_for_schedule(amount: float, schedule) -> bool:
    """Return True if amount is allowed under the schedule rules."""
    if amount == 0:
        return bool(getattr(schedule, 'allow_zero_amount', True))
    if amount < 0:
        return bool(getattr(schedule, 'allow_negative_amount', True))
    return amount > 0



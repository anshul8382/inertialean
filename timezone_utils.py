"""
Timezone utilities for Indian Standard Time (IST)
"""
from datetime import datetime
import pytz
from config import Config

# Indian Standard Time
IST = pytz.timezone('Asia/Kolkata')
UTC = pytz.UTC

def get_ist_now():
    """Get current time in IST"""
    return datetime.now(IST)

def utc_to_ist(utc_datetime):
    """Convert UTC datetime to IST"""
    if utc_datetime is None:
        return None
    
    # If datetime is naive, assume it's UTC
    if utc_datetime.tzinfo is None:
        utc_datetime = UTC.localize(utc_datetime)
    
    # Convert to IST
    return utc_datetime.astimezone(IST)

def ist_to_utc(ist_datetime):
    """Convert IST datetime to UTC"""
    if ist_datetime is None:
        return None
    
    # If datetime is naive, assume it's IST
    if ist_datetime.tzinfo is None:
        ist_datetime = IST.localize(ist_datetime)
    
    # Convert to UTC
    return ist_datetime.astimezone(UTC)

def format_ist_datetime(dt, format_str='%Y-%m-%d %H:%M'):
    """Format datetime in IST"""
    if dt is None:
        return 'Not Set'
    
    # Convert to IST if needed
    if dt.tzinfo is None:
        dt = UTC.localize(dt)
    
    ist_dt = dt.astimezone(IST)
    return ist_dt.strftime(format_str)

def format_ist_date(dt, format_str='%Y-%m-%d'):
    """Format date in IST"""
    if dt is None:
        return 'Not Set'
    
    # Convert to IST if needed
    if dt.tzinfo is None:
        dt = UTC.localize(dt)
    
    ist_dt = dt.astimezone(IST)
    return ist_dt.strftime(format_str)

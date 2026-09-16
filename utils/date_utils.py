"""
Date parsing utilities - ensures dates are parsed without timezone conversion
"""
from datetime import datetime, date
import pandas as pd
import logging

logger = logging.getLogger(__name__)

def parse_date_safe(date_value, dayfirst=True):
    """
    Parse date value safely without timezone conversion
    
    Args:
        date_value: Date string, date object, or datetime object
        dayfirst: Whether to interpret day first (for ambiguous dates)
    
    Returns:
        date object (naive, no timezone)
    
    Raises:
        ValueError: If date cannot be parsed
    """
    if date_value is None or pd.isna(date_value):
        raise ValueError("Date is None or NaN")
    
    # If already a date object, return as-is
    if isinstance(date_value, date):
        return date_value
    
    # If datetime object, extract date (no timezone conversion)
    if isinstance(date_value, datetime):
        return date_value.date()
    
    # Parse string explicitly
    date_str = str(date_value).strip()
    
    # Try explicit formats first (no timezone conversion)
    date_formats = [
        '%d-%b-%Y',      # 9-May-2017
        '%d-%B-%Y',      # 9-May-2017 (full month name)
        '%d/%m/%Y',      # 09/05/2017
        '%Y-%m-%d',      # 2017-05-09
        '%d-%m-%Y',      # 09-05-2017
        '%m/%d/%Y',      # 05/09/2017
        '%Y/%m/%d',      # 2017/05/09
        '%d.%b.%Y',      # 9.May.2017
        '%d %b %Y',      # 9 May 2017
        '%d %B %Y',      # 9 May 2017 (full month)
    ]
    
    for fmt in date_formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    
    # Last resort: pandas with explicit utc=False to prevent timezone conversion
    try:
        return pd.to_datetime(date_str, dayfirst=dayfirst, utc=False).date()
    except Exception as e:
        raise ValueError(f"Unable to parse date: '{date_str}'. Error: {str(e)}")











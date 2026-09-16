"""
Simple caching utilities for performance optimization
"""
import time
from functools import wraps
from typing import Dict, Any, Optional

# Simple in-memory cache
_cache: Dict[str, Dict[str, Any]] = {}

def cache_result(expiry_seconds: int = 300):
    """
    Simple cache decorator that caches function results for a specified time
    
    Args:
        expiry_seconds: How long to cache the result (default: 5 minutes)
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Create cache key from function name and arguments
            cache_key = f"{func.__name__}:{hash(str(args) + str(sorted(kwargs.items())))}"
            
            # Check if we have a cached result
            if cache_key in _cache:
                cached_data = _cache[cache_key]
                if time.time() - cached_data['timestamp'] < expiry_seconds:
                    return cached_data['result']
            
            # Execute function and cache result
            result = func(*args, **kwargs)
            _cache[cache_key] = {
                'result': result,
                'timestamp': time.time()
            }
            
            return result
        return wrapper
    return decorator

def clear_cache():
    """Clear all cached data"""
    _cache.clear()

def get_cache_stats() -> Dict[str, Any]:
    """Get cache statistics"""
    current_time = time.time()
    active_entries = 0
    expired_entries = 0
    
    for key, data in _cache.items():
        if current_time - data['timestamp'] < 300:  # 5 minutes default
            active_entries += 1
        else:
            expired_entries += 1
    
    return {
        'total_entries': len(_cache),
        'active_entries': active_entries,
        'expired_entries': expired_entries,
        'cache_size': len(_cache)
    }

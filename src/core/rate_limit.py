"""Rate limiting module for the FastAPI application.

Creates a single Limiter instance keyed by IP address.
No business logic — configuration only.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

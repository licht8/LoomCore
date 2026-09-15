"""Rate limiting module for the FastAPI application.

Creates a single Limiter instance keyed by the real client IP (see
``src.core.client_ip``). Behind the default nginx-on-loopback deployment this
gives every client its own bucket instead of one shared proxy address.
No business logic — configuration only.
"""

from slowapi import Limiter

from src.core.client_ip import client_ip

limiter = Limiter(key_func=client_ip)

"""Модуль rate limiting для FastAPI-приложения.

Создаёт единственный экземпляр Limiter с ключом по IP-адресу.
Ниной бизнес-логики — только конфигурация.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

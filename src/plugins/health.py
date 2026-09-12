"""Фоновая проверка здоровья плагинов и автоперезапуск."""

import asyncio
import logging

from src.plugins import plugin_manager

logger = logging.getLogger("loomcore.plugins.health")

HEALTHCHECK_INTERVAL_SECONDS = 30


async def healthcheck_loop() -> None:
    """Периодически проверять плагины и перезапускать мёртвые."""
    while True:
        await asyncio.sleep(HEALTHCHECK_INTERVAL_SECONDS)
        for name in plugin_manager.plugin_names():
            if not plugin_manager.is_healthy(name):
                logger.warning(f"Плагин '{name}' не здоров — перезапуск")
                try:
                    await plugin_manager.restart_plugin(name)
                    logger.info(f"Плагин '{name}' успешно перезапущен")
                except Exception as exc:
                    logger.error(f"Не удалось перезапустить '{name}': {exc}")

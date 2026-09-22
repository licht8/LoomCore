"""Background plugin health check and auto-restart."""

import asyncio
import logging

from src.plugins import plugin_manager

logger = logging.getLogger("loomcore.plugins.health")

HEALTHCHECK_INTERVAL_SECONDS = 30
RESTART_TIMEOUT_SECONDS = 60


async def healthcheck_loop() -> None:
    """Periodically check plugins and restart the dead ones."""
    while True:
        await asyncio.sleep(HEALTHCHECK_INTERVAL_SECONDS)
        try:
            names = plugin_manager.plugin_names()
        except Exception as exc:
            logger.error(f"Failed to enumerate plugins: {exc}")
            names = []
        for name in names:
            try:
                if not plugin_manager.is_enabled(name):
                    continue
                if not plugin_manager.is_healthy(name):
                    logger.warning(f"Plugin '{name}' is unhealthy — restarting")
                    try:
                        restarted = await asyncio.wait_for(
                            plugin_manager.restart_plugin(name),
                            timeout=RESTART_TIMEOUT_SECONDS,
                        )
                        if restarted:
                            logger.info(f"Plugin '{name}' restarted successfully")
                        else:
                            logger.warning(f"Plugin '{name}' could not be restarted")
                    except TimeoutError:
                        logger.error(
                            f"Restart of '{name}' timed out after "
                            f"{RESTART_TIMEOUT_SECONDS}s"
                        )
                    except Exception as exc:
                        logger.error(f"Failed to restart '{name}': {exc}")
            except Exception as exc:
                logger.error(f"Plugin health check failed for '{name}': {exc}")

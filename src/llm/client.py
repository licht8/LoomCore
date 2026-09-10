import httpx
import logging

from src.config import settings

logger = logging.getLogger("loomhub.llm")


class HetznerClient:
    """Клиент для Hetzner Inference API."""

    BASE_URL = "https://inference.hetzner.com/api/v1"
    MODEL = settings.hetzner_model

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.hetzner_api_key
        self.base_url = settings.hetzner_base_url

    async def chat_completion(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> dict:
        """Отправить запрос к LLM и вернуть JSON-ответ."""
        payload: dict = {
            "model": self.MODEL,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools

        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            logger.info(f"📡 Запрос к LLM API: model={self.MODEL}, base_url={self.base_url}")
            try:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
            except httpx.HTTPError:
                logger.exception("❌ Ошибка запроса к LLM API")
                raise
            response.raise_for_status()
            logger.info(f"✅ LLM API response: status={response.status_code}, size={len(response.content)}")
            return response.json()

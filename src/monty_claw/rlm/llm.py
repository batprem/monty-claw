"""Model factory: any OpenAI-compatible endpoint, as a Pydantic AI model."""

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from monty_claw.config import Settings


def build_model(settings: Settings) -> Model:
    return OpenAIChatModel(
        settings.llm_model,
        provider=OpenAIProvider(base_url=settings.llm_base_url, api_key=settings.llm_api_key),
    )

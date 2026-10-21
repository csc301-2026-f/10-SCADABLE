"""Small reusable wrapper around the OpenAI Responses API."""

from typing import TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from llm.config import Settings
from llm.usage import LLMUsage, UsageRecord

OutputModel = TypeVar("OutputModel", bound=BaseModel)


class OpenAIClient:
    """Own one async SDK client; callers must close it after use."""

    def __init__(self, settings: Settings) -> None:
        api_key = settings.openai_api_key
        if api_key is None or not api_key.get_secret_value().strip():
            raise ValueError("Set OPENAI_API_KEY before constructing OpenAIClient.")
        if not settings.openai_model.strip():
            raise ValueError("Set OPENAI_MODEL before constructing OpenAIClient.")
        self.model = settings.openai_model
        self.usage = LLMUsage()
        self.sdk = AsyncOpenAI(
            api_key=api_key.get_secret_value(),
            timeout=settings.openai_timeout_seconds,
        )

    async def generate_structured(
        self,
        *,
        instructions: str,
        input_text: str,
        output_schema: type[OutputModel],
    ) -> OutputModel:
        """Send an explicit request and parse its output into a Pydantic model."""
        self.usage.request_count += 1
        response = await self.sdk.responses.parse(
            model=self.model,
            instructions=instructions,
            input=input_text,
            text_format=output_schema,
        )
        if response.usage is not None:
            usage = response.usage
            details = usage.input_tokens_details
            self.usage.responses.append(
                UsageRecord(
                    model=response.model,
                    input_tokens=usage.input_tokens,
                    cached_input_tokens=getattr(details, "cached_tokens", 0),
                    cache_write_tokens=getattr(details, "cache_write_tokens", 0),
                    output_tokens=usage.output_tokens,
                    service_tier=response.service_tier,
                )
            )
        if response.output_parsed is None:
            # Refusals and incomplete responses can have no parsed model.
            raise ValueError("OpenAI returned no parsed structured output.")
        return response.output_parsed

    async def close(self) -> None:
        """Release SDK HTTP resources without making an API request."""
        await self.sdk.close()

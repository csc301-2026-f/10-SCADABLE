"""Small reusable wrapper around the OpenAI Responses API."""
from typing import TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from llm.config import Settings
from langfuse import Langfuse
import asyncio

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
        self.sdk = AsyncOpenAI(
            api_key=api_key.get_secret_value(),
            timeout=settings.openai_timeout_seconds,
        )
        self.cost_tracker = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key.get_secret_value() if settings.langfuse_secret_key else None,
            base_url=settings.langfuse_base_url,
            tracing_enabled=bool(settings.langfuse_secret_key and settings.langfuse_public_key)
        )

    async def generate_structured(
        self,
        *,
        instructions: str,
        input_text: str,
        output_schema: type[OutputModel],
        observation_name: str="openai-api-call"
    ) -> OutputModel:
        """Send an explicit request and parse its output into a Pydantic model."""
        with self.cost_tracker.start_as_current_observation(
            as_type="generation", 
            name=observation_name, 
            model=self.model
            ) as generation:
            response = await self.sdk.responses.parse(
                model=self.model,
                instructions=instructions,
                prompt_cache_options={
                    "mode": "explicit",
                    "ttl": "30m",
                },
                input=[
                    {
                        "role": "developer",
                        "content": [
                            {
                                "type": "input_text",
                                "text": instructions,
                                "prompt_cache_breakpoint": {
                                    "mode": "explicit",
                                },
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": input_text,
                    },
                ],
                text_format=output_schema,
            )

            usage = response.usage

            if usage:
                cached = usage.input_tokens_details.cached_tokens or 0
                reasoning = usage.output_tokens_details.reasoning_tokens or 0
                generation.update(
                            output=response.output_text,
                            usage_details={
                                "input": response.usage.input_tokens - cached,
                                "output": response.usage.output_tokens - reasoning,
                                "input_cached_tokens": cached,
                                "output_reasoning_tokens": reasoning,
                            }
                        )
            
            if response.output_parsed is None:
                # Refusals and incomplete responses can have no parsed model.
                raise ValueError("OpenAI returned no parsed structured output.")

            return response.output_parsed

    async def close(self) -> None:
        """Release SDK HTTP resources without making an API request."""
        await asyncio.to_thread(self.cost_tracker.flush)
        await self.sdk.close()

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

    async def start_thread(
        self,
        *,
        instructions: str,
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Create a persistent conversation seeded with a step's instructions.

        Returns the conversation id. Pass it to generate_in_thread() for the first
        task call and for every feedback round after it.
        """
        conversation = await self.sdk.conversations.create(
            metadata=metadata or {},
            items=[
                {
                    "type": "message",
                    "role": "developer",
                    "content": instructions,
                }
            ],
        )
        return conversation.id

    async def generate_in_thread(
        self,
        *,
        conversation_id: str,
        input_text: str,
        output_schema: type[OutputModel],
        observation_name: str = "openai-api-thread-call",
    ) -> OutputModel:
        """Send input into an existing conversation; prior turns are loaded server-side."""
        return await self.generate_structured(
            input_text=input_text,
            output_schema=output_schema,
            observation_name=observation_name,
            conversation_id=conversation_id,
        )

    async def generate_structured(
        self,
        *,
        instructions: str | None = None,
        input_text: str,
        output_schema: BaseModel,
        observation_name: str="openai-api-call",
        conversation_id: str | None = None,
    ) -> BaseModel:
        """Send an explicit request and parse its output into a Pydantic model.

        Stateless by default (instructions required). With conversation_id, the
        request runs inside that conversation: its instructions were seeded by
        start_thread, and OpenAI stores both the input and the output in the thread.
        """
        input_items: list[dict] = []
        request_kwargs: dict = {}
        if conversation_id is None:
            if instructions is None:
                raise ValueError("instructions are required when no conversation_id is given.")
            request_kwargs["instructions"] = instructions
            input_items.append(
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
                }
            )
        else:
            request_kwargs["conversation"] = conversation_id
        input_items.append({"role": "user", "content": input_text})

        with self.cost_tracker.start_as_current_observation(
            as_type="generation",
            name=observation_name,
            model=self.model,
            input=input_items
            ) as generation:
            response = await self.sdk.responses.parse(
                model=self.model,
                prompt_cache_options={
                    "mode": "explicit",
                    "ttl": "30m",
                },
                input=input_items,
                text_format=output_schema,
                **request_kwargs,
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

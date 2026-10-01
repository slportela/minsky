"""One real call to the configured model: checks the key, the endpoint and the pinned model id.

Run with `make llm-smoke` (reads MINSKY_LLM_* from .env). Prints the model that answered, latency and
tokens; never the key.
"""

import asyncio

from minsky_api.config import get_settings
from minsky_api.llm.client import LLM


async def main() -> None:
    settings = get_settings()
    result = await LLM(settings).respond(
        "Respondé en una sola oración, en el idioma del usuario.",
        [{"role": "user", "content": "Hola, ¿con qué modelo estoy hablando?"}],
        max_output_tokens=64,
    )
    print(f"endpoint: {settings.llm_base_url}")
    print(f"model:    {result.model} (pinned: {settings.llm_model})")
    print(f"latency:  {result.latency_ms:.0f} ms · tokens in/out: {result.input_tokens}/{result.output_tokens}")
    print(f"reply:    {result.text}")


if __name__ == "__main__":
    asyncio.run(main())

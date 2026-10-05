"""Real calls to the configured model: checks the key, the endpoint and the pinned model id.

`make llm-smoke` (reads MINSKY_LLM_* from .env) makes one plain call. `make llm-smoke-tools` also runs the tool
round trip the agentic search depends on: the model calls a tool, the result goes back as a `function_call_output`
item without provider ids (as `LLM.step` sends it), and the model answers with it. It costs a few cents at most.
Both print the model that answered, latency and tokens; never the key.
"""

import argparse
import asyncio
import json
import sys
from typing import Any

from openai import OpenAIError

from minsky_api.config import get_settings
from minsky_api.llm.client import LLM, ToolSpec

_INSTRUCTIONS = (
    "Eres un asistente breve. Para saber la temperatura de una ciudad debes usar la herramienta lookup_temperature; "
    "no inventes datos. Cuando tengas el resultado, responde en una frase."
)
_TOOL = ToolSpec(
    name="lookup_temperature",
    description="Temperature in Celsius for a city.",
    parameters={
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
        "additionalProperties": False,
    },
)
_RESULT = '{"celsius": 22}'


class SmokeFailure(RuntimeError):
    """A stage of the round trip did not do what the agentic search needs; `lines` is what happened before."""

    def __init__(self, message: str, lines: list[str]) -> None:
        super().__init__(message)
        self.lines = lines


async def tool_round_trip(llm: LLM) -> list[str]:
    """Call a tool, answer it, and read the reply. Raises SmokeFailure at the first stage that goes wrong."""
    items: list[dict[str, Any]] = [{"role": "user", "content": "¿Qué temperatura hace ahora en Lima?"}]
    first = await llm.step(_INSTRUCTIONS, items, tools=[_TOOL], max_output_tokens=1024)
    lines = [
        f"step 1: {len(first.tool_calls)} tool call(s) · {first.input_tokens}/{first.output_tokens} tokens · "
        f"{first.latency_ms:.0f} ms · model {first.model}"
    ]
    if not first.tool_calls:
        raise SmokeFailure("step 1: the model did not call the tool", lines)
    call = first.tool_calls[0]
    try:
        arguments = json.loads(call.arguments)
    except json.JSONDecodeError as error:
        raise SmokeFailure(f"step 1: the tool arguments are not JSON: {call.arguments[:80]!r}", lines) from error
    lines.append(f"        {call.name}({arguments})")
    items += [
        {"type": "function_call", "call_id": call.call_id, "name": call.name, "arguments": call.arguments},
        {"type": "function_call_output", "call_id": call.call_id, "output": _RESULT},
    ]
    second = await llm.step(_INSTRUCTIONS, items, tools=[_TOOL], max_output_tokens=1024)
    lines.append(
        f"step 2: {len(second.tool_calls)} tool call(s) · {second.input_tokens}/{second.output_tokens} tokens · "
        f"{second.latency_ms:.0f} ms"
    )
    lines.append(f"        reply: {second.text}")
    if second.tool_calls:
        raise SmokeFailure("step 2: the model called the tool again instead of answering", lines)
    if "22" not in second.text:
        raise SmokeFailure("step 2: the reply does not use the tool result", lines)
    return lines


async def run(*, tools: bool) -> int:
    settings = get_settings()
    llm = LLM(settings)
    result = await llm.respond(
        "Respondé en una sola oración, en el idioma del usuario.",
        [{"role": "user", "content": "Hola, ¿con qué modelo estoy hablando?"}],
        max_output_tokens=64,
    )
    print(f"endpoint: {settings.llm_base_url}")
    print(f"model:    {result.model} (pinned: {settings.llm_model})")
    print(f"latency:  {result.latency_ms:.0f} ms · tokens in/out: {result.input_tokens}/{result.output_tokens}")
    print(f"reply:    {result.text}")
    if not tools:
        return 0
    try:
        for line in await tool_round_trip(llm):
            print(line)
    except SmokeFailure as failure:
        for line in failure.lines:
            print(line)
        print(f"FAILED {failure}")
        return 1
    except OpenAIError as error:
        message = str(error)[:500]
        print(f"FAILED the provider refused the tool round trip: {type(error).__name__}: {message}")
        if "reasoning" in message.lower():
            print(
                "hint: the model wants its reasoning items replayed with the tool call. LLM.step sends none; "
                "it must ask for reasoning.encrypted_content and send those items back."
            )
        return 1
    print("tool round trip: ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", action="store_true", help="also run the tool call round trip (agentic search)")
    args = parser.parse_args(argv)
    return asyncio.run(run(tools=args.tools))


if __name__ == "__main__":
    sys.exit(main())

"""
llm_client.py — Couche d'abstraction LLM : factory OpenAI / Anthropic Foundry.

Tous les appelants (video_agent, prompt_enhancer, modules) passent par
`create_llm_client(model_config)` au lieu de `OpenAI(...)` en dur. Si le
provider est Foundry (provider_id commence par "foundry"), on renvoie un
adaptateur qui traduit le contrat OpenAI (messages, tools, tool_calls) en
appels Anthropic Messages API. Sinon → client OpenAI classique.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

DEFAULT_MAX_TOKENS = 4096


# ---------------------------------------------------------------------------
# Détection du provider Foundry
# ---------------------------------------------------------------------------
def is_foundry_provider(model_config: dict) -> bool:
    pid = (model_config or {}).get("provider_id", "")
    return pid.startswith("foundry")


# ---------------------------------------------------------------------------
# Factory publique
# ---------------------------------------------------------------------------
def create_llm_client(model_config: dict):
    """Retourne un client LLM compatible `client.chat.completions.create(...)`.
    OpenAI pour les providers classiques, FoundryAdapter pour Anthropic Foundry."""
    provider = model_config["provider"]
    if is_foundry_provider(model_config):
        return FoundryAdapter(
            api_key=provider["token"],
            resource=provider["base_url"],
        )
    return OpenAI(api_key=provider["token"], base_url=provider["base_url"])


# ═══════════════════════════════════════════════════════════════════════════
# Réponse « façon OpenAI » (dataclasses légères)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class _Function:
    name: str
    arguments: str


@dataclass
class _ToolCall:
    id: str
    function: _Function
    type: str = "function"


@dataclass
class _Message:
    content: str | None = None
    tool_calls: list[_ToolCall] | None = None


@dataclass
class _Choice:
    message: _Message
    finish_reason: str | None = None


@dataclass
class _Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class _ChatResponse:
    choices: list[_Choice] = field(default_factory=list)
    usage: _Usage = field(default_factory=_Usage)


# ═══════════════════════════════════════════════════════════════════════════
# Conversion messages / tools / tool_choice : OpenAI → Anthropic
# ═══════════════════════════════════════════════════════════════════════════

def _convert_messages(messages: list[dict]) -> tuple[str | None, list[dict]]:
    """OpenAI messages → (system_text, anthropic_messages)."""
    system_parts: list[str] = []
    out: list[dict] = []

    for msg in messages:
        role = msg.get("role")

        if role in ("system", "developer"):
            system_parts.append(msg.get("content", ""))
            continue

        if role == "user":
            out.append({"role": "user", "content": msg.get("content", "")})
            continue

        if role == "assistant":
            blocks: list[dict] = []
            text = (msg.get("content") or "").strip()
            if text:
                blocks.append({"type": "text", "text": text})
            for tc in msg.get("tool_calls") or []:
                func = tc.get("function", {})
                try:
                    inp = json.loads(func.get("arguments", "{}"))
                except json.JSONDecodeError:
                    inp = {}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": func.get("name", ""),
                    "input": inp,
                })
            if not blocks:
                blocks = [{"type": "text", "text": " "}]
            out.append({"role": "assistant", "content": blocks})
            continue

        if role == "tool":
            result_block = {
                "type": "tool_result",
                "tool_use_id": msg.get("tool_call_id", ""),
                "content": msg.get("content", ""),
            }
            if (out and out[-1]["role"] == "user"
                    and isinstance(out[-1]["content"], list)
                    and all(isinstance(b, dict) and b.get("type") == "tool_result"
                            for b in out[-1]["content"])):
                out[-1]["content"].append(result_block)
            else:
                out.append({"role": "user", "content": [result_block]})

    system = "\n\n".join(system_parts) if system_parts else None
    return system, out


def _convert_tools(tools: list[dict] | None) -> list[dict] | None:
    """OpenAI tool schemas → Anthropic format."""
    if not tools:
        return None
    result = []
    for tool in tools:
        func = tool.get("function", tool)
        entry: dict[str, Any] = {
            "name": func.get("name", ""),
            "input_schema": func.get("parameters", {"type": "object"}),
        }
        desc = func.get("description")
        if desc:
            entry["description"] = desc
        result.append(entry)
    return result


def _convert_tool_choice(tool_choice: Any) -> dict | None:
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        mapping = {"auto": "auto", "none": "none", "required": "any"}
        tc_type = mapping.get(tool_choice)
        return {"type": tc_type} if tc_type else None
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        return {"type": "tool", "name": tool_choice["function"]["name"]}
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Adaptateur Foundry
# ═══════════════════════════════════════════════════════════════════════════

class _FoundryChatCompletions:
    """Mime `client.chat.completions.create(...)` (interface OpenAI) en
    déléguant au SDK Anthropic Messages."""

    def __init__(self, anthropic_client):
        self._client = anthropic_client

    def create(self, *, model: str, messages: list, tools: list | None = None,
               tool_choice: Any = None, max_tokens: int = DEFAULT_MAX_TOKENS,
               temperature: float | None = None, **_kwargs) -> _ChatResponse:

        system, anthro_msgs = _convert_messages(messages)

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": anthro_msgs,
        }
        if system:
            kwargs["system"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature

        anthro_tools = _convert_tools(tools)
        if anthro_tools:
            kwargs["tools"] = anthro_tools

        anthro_tc = _convert_tool_choice(tool_choice)
        if anthro_tc:
            kwargs["tool_choice"] = anthro_tc

        response = self._client.messages.create(**kwargs)

        # --- Réponse → format OpenAI ---
        text_parts: list[str] = []
        tool_calls: list[_ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(_ToolCall(
                    id=block.id,
                    function=_Function(
                        name=block.name,
                        arguments=json.dumps(block.input),
                    ),
                ))

        content = "".join(text_parts) if text_parts else None

        finish = "stop"
        if response.stop_reason == "tool_use":
            finish = "tool_calls"
        elif response.stop_reason == "max_tokens":
            finish = "length"

        usage = _Usage(
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
            total_tokens=response.usage.input_tokens + response.usage.output_tokens,
        )

        return _ChatResponse(
            choices=[_Choice(message=_Message(content=content,
                                              tool_calls=tool_calls or None),
                             finish_reason=finish)],
            usage=usage,
        )


class _FoundryChat:
    def __init__(self, anthropic_client):
        self.completions = _FoundryChatCompletions(anthropic_client)


class FoundryAdapter:
    """Adaptateur Anthropic Foundry → interface OpenAI `client.chat.completions.create`.

    `resource` = identifiant du déploiement Foundry (le champ `base_url` du provider).
    `api_key`  = clé API Foundry."""

    def __init__(self, api_key: str, resource: str):
        from anthropic import AnthropicFoundry
        self._client = AnthropicFoundry(
            api_key=api_key,
            resource=resource,
            max_retries=2,
            timeout=10 * 60,
        )
        self.chat = _FoundryChat(self._client)

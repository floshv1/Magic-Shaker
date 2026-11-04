"""Fournisseurs LLM : API Claude (SDK officiel `anthropic`) ou Ollama en local (HTTP).

Les deux exposent la même interface minimale :
  - `chat_with_tools(system, question, history, ctx)` : boucle d'appels d'outils jusqu'à la réponse ;
  - `structured(system, prompt, schema)` : une réponse JSON conforme au schéma.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from p9.config import LlmSettings

from .tools import TOOLS, ToolContext, run_tool

MAX_TOOL_ROUNDS = 12


class LlmUnavailable(RuntimeError):
    """Fournisseur non configuré ou injoignable (clé absente, Ollama arrêté…)."""


@dataclass
class ToolCall:
    name: str
    input: dict[str, Any]
    output: str
    is_error: bool


@dataclass
class ChatResult:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    stop_reason: str = ""


# =========================================================================== Claude

class AnthropicProvider:
    """API Claude. Modèle par défaut claude-opus-5-5, effort réglable, repli serveur en cas de refus."""

    name = "anthropic"
    # Repli côté serveur si le modèle refuse (routage par catégorie, sans liste de modèles à maintenir).
    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(self, cfg: LlmSettings) -> None:
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover - dépendance du projet
            raise LlmUnavailable("SDK anthropic non installe (uv sync)") from e
        self._anthropic = anthropic
        self.model = cfg.anthropic_model
        self.effort = cfg.effort
        self.client = anthropic.Anthropic()

    def _tools(self) -> list[dict[str, Any]]:
        return [{"name": t.name, "description": t.description, "input_schema": t.parameters, "strict": True}
                for t in TOOLS]

    def _create(self, **kwargs: Any):
        a = self._anthropic
        output_config = {"effort": self.effort, **kwargs.pop("output", {})}
        try:
            return self.client.beta.messages.create(
                model=self.model, max_tokens=16000, output_config=output_config,
                betas=[self.FALLBACK_BETA], fallbacks="default", **kwargs)
        except a.AuthenticationError as e:
            raise LlmUnavailable("cle API Claude invalide ou absente (ANTHROPIC_API_KEY dans .env)") from e
        except a.PermissionDeniedError as e:
            raise LlmUnavailable(f"acces refuse par l'API : {e.message}") from e
        except a.RateLimitError as e:
            raise LlmUnavailable("limite de debit atteinte, reessayer dans un instant") from e
        except a.APIConnectionError as e:
            raise LlmUnavailable("API Claude injoignable (reseau)") from e
        except a.APIStatusError as e:
            raise LlmUnavailable(f"erreur API ({e.status_code}) : {e.message}") from e

    def chat_with_tools(self, system: str, question: str, history: list[dict[str, Any]],
                        ctx: ToolContext) -> ChatResult:
        messages: list[dict[str, Any]] = [*history, {"role": "user", "content": question}]
        calls: list[ToolCall] = []
        tools = self._tools()
        for _ in range(MAX_TOOL_ROUNDS):
            resp = self._create(system=system, tools=tools, messages=messages)
            if resp.stop_reason == "refusal":
                return ChatResult(text="Le modele a refuse de repondre a cette question.", tool_calls=calls,
                                  provider=self.name, model=resp.model, stop_reason="refusal")
            if resp.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": resp.content})
                continue
            uses = [b for b in resp.content if b.type == "tool_use"]
            if resp.stop_reason != "tool_use" or not uses:
                text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
                return ChatResult(text=text, tool_calls=calls, provider=self.name, model=resp.model,
                                  stop_reason=resp.stop_reason or "")
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for u in uses:
                out, err = run_tool(ctx, u.name, u.input if isinstance(u.input, dict) else {})
                calls.append(ToolCall(u.name, dict(u.input or {}), out, err))
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": out, "is_error": err})
            messages.append({"role": "user", "content": results})   # tous les résultats dans un seul message
        return ChatResult(text="Nombre maximal d'appels d'outils atteint sans reponse finale.", tool_calls=calls,
                          provider=self.name, model=self.model, stop_reason="max_rounds")

    def structured(self, system: str, prompt: str, schema: dict[str, Any]) -> str:
        resp = self._create(system=system, messages=[{"role": "user", "content": prompt}],
                            output={"format": {"type": "json_schema", "schema": schema}})
        if resp.stop_reason == "refusal":
            raise LlmUnavailable("le modele a refuse la demande d'enrichissement")
        return next((b.text for b in resp.content if b.type == "text"), "")


# =========================================================================== Ollama

class OllamaProvider:
    """Ollama local (aucune donnée ne quitte la machine). API HTTP /api/chat avec outils."""

    name = "ollama"

    def __init__(self, cfg: LlmSettings) -> None:
        self.host = cfg.ollama_host.rstrip("/")
        self.model = cfg.ollama_model

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(f"{self.host}/api/chat", data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:  # noqa: S310 (hôte configuré)
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise LlmUnavailable(f"Ollama : erreur {e.code} ({e.read().decode('utf-8', 'replace')[:200]})") from e
        except (urllib.error.URLError, OSError) as e:
            raise LlmUnavailable(f"Ollama injoignable sur {self.host} (lancer `ollama serve`)") from e

    def chat_with_tools(self, system: str, question: str, history: list[dict[str, Any]],
                        ctx: ToolContext) -> ChatResult:
        tools = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                   "parameters": t.parameters}} for t in TOOLS]
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}, *history,
                                          {"role": "user", "content": question}]
        calls: list[ToolCall] = []
        for _ in range(MAX_TOOL_ROUNDS):
            data = self._post({"model": self.model, "messages": messages, "tools": tools, "stream": False})
            msg = data.get("message", {})
            tcalls = msg.get("tool_calls") or []
            if not tcalls:
                return ChatResult(text=(msg.get("content") or "").strip(), tool_calls=calls, provider=self.name,
                                  model=self.model, stop_reason=data.get("done_reason", ""))
            messages.append(msg)
            for tc in tcalls:
                fn = tc.get("function", {})
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                out, err = run_tool(ctx, fn.get("name", ""), args)
                calls.append(ToolCall(fn.get("name", ""), args, out, err))
                messages.append({"role": "tool", "content": out, "tool_name": fn.get("name", "")})
        return ChatResult(text="Nombre maximal d'appels d'outils atteint sans reponse finale.", tool_calls=calls,
                          provider=self.name, model=self.model, stop_reason="max_rounds")

    def structured(self, system: str, prompt: str, schema: dict[str, Any]) -> str:
        data = self._post({"model": self.model, "stream": False, "format": schema,
                           "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]})
        return data.get("message", {}).get("content", "")


def make_provider(cfg: LlmSettings) -> AnthropicProvider | OllamaProvider:
    if cfg.provider == "ollama":
        return OllamaProvider(cfg)
    if cfg.provider == "anthropic":
        return AnthropicProvider(cfg)
    raise LlmUnavailable(f"fournisseur LLM inconnu : {cfg.provider!r} (anthropic | ollama)")


def provider_status(cfg: LlmSettings) -> tuple[bool, str]:
    """Disponibilité sans appel payant : clé présente (Claude) ou serveur joignable (Ollama)."""
    if cfg.provider == "anthropic":
        has_cred = any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"))
        return (True, f"Claude ({cfg.anthropic_model})") if has_cred else (
            False, "API Claude : definir ANTHROPIC_API_KEY dans .env, ou P9_LLM_PROVIDER=ollama pour le mode local")
    if cfg.provider == "ollama":
        try:
            with urllib.request.urlopen(f"{cfg.ollama_host.rstrip('/')}/api/tags", timeout=2):  # noqa: S310
                return True, f"Ollama local ({cfg.ollama_model})"
        except (urllib.error.URLError, OSError):
            return False, f"Ollama injoignable sur {cfg.ollama_host}"
    return False, f"fournisseur inconnu : {cfg.provider}"

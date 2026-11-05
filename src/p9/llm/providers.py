"""Fournisseurs LLM : API Claude (SDK officiel `anthropic`) ou Ollama en local (HTTP).

Les deux exposent la même interface minimale :
  - `chat_with_tools(system, question, history, ctx)` : boucle d'appels d'outils jusqu'à la réponse ;
  - `structured(system, prompt, schema)` : une réponse JSON conforme au schéma.

Les deux acceptent `on_event(kind, text)` (optionnel) pour suivre l'avancement en direct :
  - kind = "step"   : une étape (appel au modèle, n° de tour) ;
  - kind = "tool"   : un outil appelé par le modèle et la taille de sa réponse ;
  - kind = "tokens" : compteur de génération en cours (Ollama, en streaming), remplace le précédent.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from p9.config import LlmSettings

from .tools import TOOLS, ToolContext, run_tool

MAX_TOOL_ROUNDS = 12

OnEvent = Callable[[str, str], None]


def _emit(on_event: OnEvent | None, kind: str, text: str) -> None:
    if on_event is not None:
        on_event(kind, text)


def _args_preview(args: dict[str, Any], limit: int = 60) -> str:
    txt = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return txt if len(txt) <= limit else txt[: limit - 1] + "…"


def _tool_event(on_event: OnEvent | None, name: str, args: dict[str, Any], out: str, err: bool) -> None:
    status = "erreur" if err else f"{len(out)} caracteres de resultat"
    _emit(on_event, "tool", f"Outil `{name}`({_args_preview(args)}) : {status}")


class LlmUnavailable(RuntimeError):
    """Fournisseur non configuré ou injoignable (clé absente, Ollama arrêté…)."""


class _Transient(LlmUnavailable):
    """Erreur serveur passagère (ex. « Internal Server Error » d'un modèle cloud Ollama) : on réessaie."""


_TRANSIENT_MARKERS = ("internal server error", "server error", "overloaded", "unavailable", "timed out",
                      "timeout", "try again", "502", "503", "504")


def _is_transient(message: str) -> bool:
    return any(m in message.lower() for m in _TRANSIENT_MARKERS)


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
                        ctx: ToolContext, on_event: OnEvent | None = None) -> ChatResult:
        messages: list[dict[str, Any]] = [*history, {"role": "user", "content": question}]
        calls: list[ToolCall] = []
        tools = self._tools()
        for rnd in range(1, MAX_TOOL_ROUNDS + 1):
            _emit(on_event, "step", f"Appel a Claude ({self.model}), tour {rnd}"
                  + (" : le modele choisit les outils a interroger" if rnd == 1 else " : lecture des resultats"))
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
                _tool_event(on_event, u.name, dict(u.input or {}), out, err)
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": out, "is_error": err})
            messages.append({"role": "user", "content": results})   # tous les résultats dans un seul message
        return ChatResult(text="Nombre maximal d'appels d'outils atteint sans reponse finale.", tool_calls=calls,
                          provider=self.name, model=self.model, stop_reason="max_rounds")

    def structured(self, system: str, prompt: str, schema: dict[str, Any], on_event: OnEvent | None = None) -> str:
        _emit(on_event, "step", f"Appel a Claude ({self.model}) : analyse et redaction des propositions "
                                "(une seule requete, la reponse arrive d'un bloc)")
        resp = self._create(system=system, messages=[{"role": "user", "content": prompt}],
                            output={"format": {"type": "json_schema", "schema": schema}})
        if resp.stop_reason == "refusal":
            raise LlmUnavailable("le modele a refuse la demande d'enrichissement")
        return next((b.text for b in resp.content if b.type == "text"), "")


# =========================================================================== Ollama

class OllamaProvider:
    """Ollama local (aucune donnée ne quitte la machine). API HTTP /api/chat avec outils."""

    name = "ollama"
    RETRIES = 2              # nouveaux essais sur erreur serveur passagère (modèles cloud surtout)

    def __init__(self, cfg: LlmSettings) -> None:
        self.host = cfg.ollama_host.rstrip("/")
        self.model = cfg.ollama_model

    def _post(self, payload: dict[str, Any], on_event: OnEvent | None = None) -> dict[str, Any]:
        """`_post_once`, réessayé après une erreur serveur passagère (1 s, puis 2 s)."""
        for attempt in range(1, self.RETRIES + 2):
            try:
                return self._post_once(payload, on_event)
            except _Transient as e:
                if attempt > self.RETRIES:
                    raise LlmUnavailable(f"{e} (apres {attempt} essais)") from e
                _emit(on_event, "step", f"Erreur passagere d'Ollama ({e}), nouvel essai {attempt}/{self.RETRIES}")
                time.sleep(attempt)
        raise AssertionError("inatteignable")  # pragma: no cover

    def _post_once(self, payload: dict[str, Any], on_event: OnEvent | None = None) -> dict[str, Any]:
        """POST /api/chat en streaming (NDJSON) ; renvoie un message agrégé comme en mode non streamé.

        Le streaming sert à suivre la génération : tokens de réflexion, puis tokens de réponse."""
        payload = {**payload, "stream": True}
        req = urllib.request.Request(f"{self.host}/api/chat", data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        content: list[str] = []
        thinking: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        n_think = n_out = 0
        last = 0.0
        final: dict[str, Any] = {}
        try:
            with urllib.request.urlopen(req, timeout=300) as r:  # noqa: S310 (hôte configuré)
                for raw in r:
                    if not raw.strip():
                        continue
                    chunk = json.loads(raw.decode("utf-8"))
                    if chunk.get("error"):
                        err = str(chunk["error"])
                        raise (_Transient if _is_transient(err) else LlmUnavailable)(f"Ollama : {err}")
                    msg = chunk.get("message") or {}
                    if msg.get("thinking"):
                        thinking.append(msg["thinking"])
                        n_think += 1
                    if msg.get("content"):
                        content.append(msg["content"])
                        n_out += 1
                    tool_calls.extend(msg.get("tool_calls") or [])
                    now = time.monotonic()
                    if now - last > 0.3:
                        last = now
                        phase = "redaction de la reponse" if n_out else "reflexion"
                        _emit(on_event, "tokens", f"{self.model}, {phase} : {n_think} tokens de reflexion, "
                                                  f"{n_out} tokens de reponse")
                    if chunk.get("done"):
                        final = chunk
                        break
            _emit(on_event, "tokens", f"{self.model} : {n_think} tokens de reflexion, {n_out} tokens de reponse")
            message: dict[str, Any] = {"role": "assistant", "content": "".join(content)}
            if tool_calls:
                message["tool_calls"] = tool_calls
            if thinking:
                message["thinking"] = "".join(thinking)
            return {"message": message, "done_reason": final.get("done_reason", "")}
        except urllib.error.HTTPError as e:
            err = f"Ollama : erreur {e.code} ({e.read().decode('utf-8', 'replace')[:200]})"
            raise (_Transient if e.code >= 500 or e.code == 429 else LlmUnavailable)(err) from e
        except (urllib.error.URLError, OSError) as e:
            raise LlmUnavailable(f"Ollama injoignable sur {self.host} (lancer `ollama serve`)") from e

    def chat_with_tools(self, system: str, question: str, history: list[dict[str, Any]],
                        ctx: ToolContext, on_event: OnEvent | None = None) -> ChatResult:
        tools = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                   "parameters": t.parameters}} for t in TOOLS]
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}, *history,
                                          {"role": "user", "content": question}]
        calls: list[ToolCall] = []
        for rnd in range(1, MAX_TOOL_ROUNDS + 1):
            _emit(on_event, "step", f"Appel a Ollama ({self.model}), tour {rnd}"
                  + (" : le modele choisit les outils a interroger" if rnd == 1 else " : lecture des resultats"))
            data = self._post({"model": self.model, "messages": messages, "tools": tools}, on_event)
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
                _tool_event(on_event, fn.get("name", ""), args, out, err)
                messages.append({"role": "tool", "content": out, "tool_name": fn.get("name", "")})
        return ChatResult(text="Nombre maximal d'appels d'outils atteint sans reponse finale.", tool_calls=calls,
                          provider=self.name, model=self.model, stop_reason="max_rounds")

    def structured(self, system: str, prompt: str, schema: dict[str, Any], on_event: OnEvent | None = None) -> str:
        _emit(on_event, "step", f"Appel a Ollama ({self.model}) : analyse et redaction des propositions")
        data = self._post({"model": self.model, "format": schema,
                           "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]},
                          on_event)
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

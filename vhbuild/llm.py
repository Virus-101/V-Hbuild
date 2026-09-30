"""The models V-Hbuild can plan with.

The default is a local model served by Ollama on this machine - any model,
including your own GGUF (see models/README.md). Claude is available as an
optional second provider. Both are asked the same thing the same way - "fill
in this schema" - so the planner and the firmware writer do not care which one
answered.

A small local model cannot be trusted to type a valid part id, so the schema
is not a suggestion here: Ollama compiles it into a grammar and the model is
unable to emit anything outside it. Pydantic validates the result again anyway, and a
failed validation gets one retry with the error shown to the model.
"""
import json
import os
import urllib.error
import urllib.request

from pydantic import BaseModel, ValidationError

TIMEOUT = float(os.environ.get("VHBUILD_LLM_TIMEOUT", "600"))


# Read at call time, so the desktop app's settings take effect without a restart.
def ollama_url() -> str:
    return os.environ.get("VHBUILD_OLLAMA_URL", "http://localhost:11434").rstrip("/")


def local_model_name() -> str:
    return os.environ.get("VHBUILD_LOCAL_MODEL", "llama3.2:3b")


def claude_model() -> str:
    return os.environ.get("VHBUILD_MODEL", "claude-opus-5")


class ProviderError(RuntimeError):
    pass


def inline_refs(schema: dict) -> dict:
    """Pydantic nests models under $defs; resolve them so any grammar compiler copes."""
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            return {k: walk(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node
    return walk(schema)


class Local:
    """A local model through Ollama's /api/chat, with the output held to a JSON schema."""
    name = "local"

    def __init__(self, url: str | None = None, model: str | None = None, post=None):
        self.url, self.model = (url or ollama_url()).rstrip("/"), model or local_model_name()
        self._post = post or self._http_post
        self._force_cpu = False

    @property
    def label(self) -> str:
        return f"local model ({self.model})"

    def _http_post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(self.url + path, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def available(self) -> bool:
        try:
            req = urllib.request.Request(self.url + "/api/tags")
            with urllib.request.urlopen(req, timeout=3) as r:
                names = [m.get("name", "") for m in json.loads(r.read()).get("models", [])]
        except (OSError, ValueError):
            return False
        want = self.model if ":" in self.model else self.model + ":latest"
        return want in names

    def _chat(self, messages: list[dict], schema: dict, max_tokens: int) -> str:
        options = {"temperature": 0.2, "num_ctx": 8192, "num_predict": max_tokens}
        if self._force_cpu:
            options["num_gpu"] = 0
        payload = {"model": self.model, "messages": messages, "format": schema,
                   "stream": False, "options": options}
        try:
            body = self._post("/api/chat", payload)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise ProviderError(f"The model {self.model} is not installed in Ollama. "
                                    f"Run: ollama pull {self.model}")
            # A 500 is usually Ollama's GPU runner crashing while loading the
            # model; retry on CPU once and stay there.
            if e.code >= 500 and not self._force_cpu:
                self._force_cpu = True
                return self._chat(messages, schema, max_tokens)
            raise ProviderError(f"Ollama answered {e.code} for {self.model}.")
        except TimeoutError:
            raise ProviderError(f"The local model took longer than {TIMEOUT:.0f} s. A smaller "
                                f"model, or VHBUILD_LLM_TIMEOUT, will help.")
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, ConnectionRefusedError):
                raise ProviderError(f"Ollama is not running at {self.url}. Start it, or pick "
                                    "another planner.")
            if isinstance(reason, TimeoutError):
                raise ProviderError(f"The local model took longer than {TIMEOUT:.0f} s.")
            # A connection dropped mid-request is the other face of a crashed runner.
            if not self._force_cpu:
                self._force_cpu = True
                return self._chat(messages, schema, max_tokens)
            raise ProviderError(f"The local model is not reachable at {self.url}: {e}")
        return (body.get("message") or {}).get("content") or ""

    def structured(self, system: str, user: str, out: type[BaseModel],
                   max_tokens: int = 4096) -> BaseModel:
        schema = inline_refs(out.model_json_schema())
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_error = ""
        for _ in range(2):
            text = self._chat(messages, schema, max_tokens)
            try:
                return out.model_validate_json(text)
            except ValidationError as e:
                last_error = str(e)
                messages += [{"role": "assistant", "content": text},
                             {"role": "user", "content": "That did not match the schema:\n"
                              f"{last_error[:1500]}\nReturn corrected JSON only."}]
        raise ProviderError(f"The local model did not return valid {out.__name__}: {last_error[:300]}")


class Claude:
    """Claude through the Anthropic SDK, using structured outputs."""
    name = "claude"

    def __init__(self, client=None, model: str | None = None):
        self._client, self.model = client, model or claude_model()

    @property
    def label(self) -> str:
        return f"Claude ({self.model})"

    @staticmethod
    def available() -> bool:
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return any(os.environ.get(k) for k in
                   ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"))

    def structured(self, system: str, user: str, out: type[BaseModel],
                   max_tokens: int = 16000) -> BaseModel:
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        response = self._client.beta.messages.parse(
            model=self.model,
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            # A refusal re-runs on a fallback model instead of failing the build.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_format=out,
        )
        if response.stop_reason == "refusal":
            raise ProviderError("Claude declined this request.")
        if response.parsed_output is None:
            raise ProviderError(f"Claude returned no {out.__name__} (stop reason: {response.stop_reason}).")
        return response.parsed_output


def choose(preference: str | None = None):
    """The provider to use, or None for the offline keyword planner.

    VHBUILD_PROVIDER = auto (default) | local | claude | offline.
    auto prefers the local model whenever Ollama is serving it.
    """
    pref = (preference or os.environ.get("VHBUILD_PROVIDER") or "auto").lower()
    if os.environ.get("VHBUILD_OFFLINE") or pref == "offline":
        return None
    if pref == "local":
        return Local()
    if pref == "claude":
        return Claude()
    mv = Local()
    if mv.available():
        return mv
    if Claude.available():
        return Claude()
    return None


def status() -> dict:
    mv = Local()
    return {"local": {"url": mv.url, "model": mv.model, "available": mv.available()},
            "claude": {"model": claude_model(), "available": Claude.available()},
            "preference": os.environ.get("VHBUILD_PROVIDER", "auto")}

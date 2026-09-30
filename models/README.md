# Local models

V-Hbuild plans with a model served by [Ollama](https://ollama.com). The default
is `llama3.2:3b`:

```
ollama pull llama3.2:3b
```

Any Ollama model works. Set `VHBUILD_LOCAL_MODEL` (e.g. `qwen2.5:7b`), or pick
one in the desktop app's Settings. Larger models make better part choices; the
engine keeps even a small model's plans electrically correct.

**Your own model (GGUF)**

1. Copy the GGUF here as `my-model.gguf`. GGUF files are git-ignored.
2. `ollama create my-model -f models/Modelfile.example`
3. `VHBUILD_LOCAL_MODEL=my-model:latest`

In Docker the same step is
`docker compose exec ollama ollama create my-model -f /models/Modelfile.example`.

V-Hbuild sends its own system prompt with every request. It replaces any SYSTEM
line in your Modelfile for that request, so a model tuned for another job
still works here.

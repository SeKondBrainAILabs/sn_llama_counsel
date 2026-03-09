# sn_llama_counsel ⚖

**AI Counsel for llama.cpp** — convene a panel of expert LLMs that deliberate on your task in parallel, then get a consolidated view from the chairperson.

Fork of the [llama.cpp](https://github.com/ggml-org/llama.cpp) web UI, extended with a Counsel tab.
Goal: eventually contribute this back to the llama.cpp community.

---

## Features

- **Multi-LLM parallel panel** — all council members answer simultaneously via SSE streaming
- **Chairperson synthesis** — one LLM synthesizes all member responses into a consolidated view
- **Auto-select** — the chairperson LLM picks the best council for your task
- **4 built-in councils**: General, Code Review, Research, Creative
- **Custom councils** — drop a YAML file in `counsels/` to add your own
- **Works with any OpenAI-compatible endpoint** — llama.cpp, LiteLLM, Groq, OpenAI, etc.
- **pip-installable** — `pip install sn-llama-counsel` → `llama-counsel`

---

## Quick Start

```bash
# 1. Install
pip install sn-llama-counsel

# 2. Point at your llama-server (or set LLAMA_API_BASE env var)
# Default: http://localhost:11434

# 3. Run
llama-counsel --port 5000

# 4. Open
open http://localhost:5000
# → click the ⚖ Counsel tab in the sidebar
```

---

## Configuration

Edit `config.yaml`:

```yaml
api_base: http://localhost:11434  # your llama-server or LiteLLM proxy
api_key: none                      # "sk-llama" for LiteLLM
default_model: llama-3.1-8b-instant
```

Or use environment variables:
```bash
LLAMA_API_BASE=http://localhost:4000 LLAMA_API_KEY=sk-llama llama-counsel
```

---

## Custom Councils

Add a YAML file to the `counsels/` directory:

```yaml
name: my_council
description: "My custom expert panel"
chairperson:
  model: qwq-32b
  system: "You synthesize expert views into clear, actionable guidance."
members:
  - model: llama-3.3-70b-versatile
    role: Expert A
    system: "You are an expert in X. Analyse the task from that perspective."
  - model: llama-3.1-8b-instant
    role: Expert B
    system: "You are an expert in Y. Focus on practical implications."
```

---

## Development

```bash
# Backend
pip install -e ".[dev]"
llama-counsel --reload

# Frontend (in another terminal)
cd frontend
npm install
npm run dev  # proxies /v1 → :11434, /api → :5000
```

---

## Architecture

```
Browser (SvelteKit — forked from llama.cpp webui)
  ↓
FastAPI :5000
  ├── GET  /api/counsels          → list YAML configs
  ├── GET  /api/models            → proxy to llama-server
  ├── POST /api/counsel/run       → SSE fan-out + synthesis
  └── POST /api/counsel/auto-select
  ↓
llama-server :11434  (or any OpenAI-compatible endpoint)
  ├── member 1 ─┐
  ├── member 2 ─┼── asyncio parallel
  └── member N ─┘
  └── chairperson (synthesis, sequential after members)
```

---

## Contributing

Issues and PRs welcome! Long-term goal: contribute the Counsel tab back to [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp).

---

## License

MIT — same as llama.cpp.

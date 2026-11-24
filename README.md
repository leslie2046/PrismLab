# PrismLab

AI model testing & analysis — benchmark your services, compare performance, and understand the results.

[简体中文](README_zh-Hans.md) · [Quick start](#quick-start) · [Workspaces](#accounts-and-workspaces) · [CLI](#command-line-testing) · [Documentation](docs/README.md)

PrismLab connects to services you already run or subscribe to. It does not host models. Use the web console for interactive testing and repeatable test plans, or the CLI for lightweight concurrency benchmarks.

## What you can test

| Test type | Workload | Key metrics |
| --- | --- | --- |
| LLM | Streaming text generation | TTFT, TPOT, tokens/s, request latency |
| Embedding | Text-to-vector requests | Latency, QPS, success rate |
| Reranker | Query and candidate documents | Latency, QPS, success rate |
| Audio | Speech-to-text transcription | Latency, QPS; RTF when audio duration is known |
| Dify Retrieve | Knowledge base retrieval | Latency, QPS, success rate |
| Dify Chat | Streaming application responses | First-message latency, total latency, success rate |

The console includes model search and type filters, YAML-driven LLM parameters, scheduled test plans, run history, comparison charts, and manually generated AI performance reports. It supports English, Simplified Chinese, and light/dark themes.

- **Sign-in:** local password, GitHub OAuth, and GitLab or another OIDC provider.
- **Isolated workspaces:** private personal spaces and shared teams with Owner, administrator, and member roles.
- **Collaboration:** invite specific existing users, switch spaces, and customize your nickname and avatar.
- **Responsive data loading:** paginated lists, independently loaded home-page data, and compressed responses in the Docker deployment.

The web application uses **PostgreSQL**; SQLite is supported only as a source for the one-time migration tool. Docker Compose includes PostgreSQL 16, a quality-evaluation worker, and a durable application worker. Redis is not required.

Provider presets include Moonshot (Kimi), Qwen, MiniMax, Volcengine Ark, DeepSeek, SiliconFlow, Xinference, vLLM, Alibaba Cloud, Xunfei MaaS, and Huawei Cloud. Qwen is a separate provider from Alibaba Cloud Model Studio; existing configurations are unchanged. Available test types and model discovery depend on the provider and deployed model; not every provider supports every type. See [provider setup](docs/model-discovery.md#新增云供应商).

## Quick start

Choose **Docker** for deployment or **local development** to modify the code. You need a reachable model service or Dify instance for actual testing.

### Option A: Docker

From the repository root, copy `docker/.env.example` to `docker/.env` and configure sign-in and PostgreSQL as described in the [Docker guide](docker/README.md). Then start the stack.

```bash
docker compose --env-file docker/.env -f docker/compose.yaml up --build -d
```

Open [http://localhost:8080](http://localhost:8080), sign in, and select a workspace. The [Docker guide](docker/README.md) covers public deployment, offline packaging, and backups.

### Option B: Local development

Install the tools listed in [dependency management](docs/dependencies.md) and prepare [PostgreSQL](docs/postgresql.md). Start the API from the repository root.

```bash
uv sync --locked
export DATABASE_URL=postgresql://prismlab:prismlab@127.0.0.1:5432/prismlab
uv run --no-sync python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

Start the web console in a second terminal.

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm run dev --host 127.0.0.1
```

Open [http://localhost:5173](http://localhost:5173). Vite proxies API requests to port 8000.

## Accounts and workspaces

With application sign-in enabled, each account has a private personal workspace and can join teams. Model settings, test plans, and evaluation datasets belong to the selected workspace. See [sign-in and SSO](docs/authentication.md) and [workspaces and permissions](docs/tenancy.md).

## Your first test

1. **Model providers:** select a built-in provider, add a model and configure its endpoint and credentials, or fetch and select models where supported. Save the configuration.
2. **Playground:** select a test type and configured model, then send one request to verify the setup. This makes a real service call but does not create a test plan or run record.
3. **Test plans:** create a plan with your inputs, target models, concurrency levels, and request count. Start small, such as concurrency 1 and 5 requests. Saving a new plan does not start it; start it when ready.
4. **Run history:** inspect progress, failures, and results. Each execution creates a separate run record.
5. **Analysis dashboard:** compare concurrency levels or repeated runs of the same plan. Use comparable inputs and generation settings for meaningful comparisons.

For Dify, use **Dify configuration** instead of Model providers. The default service URL is `https://api.dify.ai`; replace it for self-hosted instances. Retrieval requires a knowledge base ID and Dataset API Key; Chat uses an application API Key. The refresh button retrieves the resource name without saving. Plans and Playground reuse the knowledge base ID from the configuration.

**Testing calls real APIs and may incur charges.** Verify one request before increasing concurrency or enabling recurring schedules.

## LLM Playground and AI reports

Playground supports single-request streaming, a system prompt, and model-specific parameters. See [LLM model configuration](docs/llm-model-configuration.md) for thinking controls, parameter mappings, and defaults.

Configure the analysis model and thinking mode in **Space settings → AI test reports**, then generate a report from a finished run. Reports use performance statistics and persist in PostgreSQL. Failed reanalysis leaves the previous report available.

## RAG evaluation datasets

Generate samples from documents or Dify knowledge bases, or import, edit, and export existing samples. Use a dataset to evaluate answers from an LLM or Dify Chat, or retrieval results from a Dify knowledge base. See the [dataset guide](docs/rag-datasets.md) and [quality evaluation guide](docs/quality-evaluations.md) in Chinese.

## Command-line testing

The CLI reads service settings from the process environment and the optional `cli/.env` file. Configure an endpoint and credentials, then run from the repository root.

```bash
uv sync --locked --only-group cli
uv run --no-sync python -m cli.perf_llm --provider deepseek -c 1 -n 5 --json-report output/llm.json
```

See the [CLI configuration reference](docs/cli-reference.md) for other workloads, options, JSON output, and environment variables.

## Understanding the metrics

| Metric | Meaning |
| --- | --- |
| P50 / P95 / P99 | Latency percentiles: 50% / 95% / 99% of measured successful requests finish within this time |
| QPS | Throughput; `qps_success` in JSON is successful requests divided by elapsed wall time |
| TTFT | Client-observed time from request start to first generated content, including reasoning |
| TPOT | `(request latency − TTFT) / (output tokens − 1)` |
| tokens/s | `1000 / TPOT`, excluding the wait for the first token |
| Audio RTF | Request latency divided by audio duration; lower is faster |

LLM TPOT and tokens/s require server-reported output-token usage. Missing usage or insufficient tokens makes them unavailable; stream chunks are not counted as tokens. UI values are rounded to integers; reports retain precision.

The web console, CLI terminal summaries, and exported JSON all calculate latency from successful requests and QPS as successful requests divided by elapsed wall time.

These are **client-side end-to-end measurements**, including network overhead—not pure model inference times. Small samples are useful for checking connectivity, not stable P95/P99 conclusions. Percentiles should not be averaged across runs.

## Data and security

PrismLab encrypts stored credentials with Fernet. Preserve the database, data directory, and externally supplied secrets together in backups. See [PostgreSQL and backups](docs/postgresql.md) and [sign-in and sessions](docs/authentication.md).

## Development and documentation

Use the [documentation index](docs/README.md) for usage, deployment, and development guides. See [repository layout and entry points](docs/repository-layout.md) and [dependency management](docs/dependencies.md) for development and test commands. The [roadmap](ROADMAP.md) lists open work.

## License

[MIT](LICENSE)

# PrismLab · 棱镜实验室

AI 模型测试与分析平台：验证服务、对比性能，让测试结果更容易理解。

[English](README.md) · [快速开始](#快速开始) · [账号与空间](#账号与空间) · [命令行测试](#命令行测试) · [文档索引](docs/README.md)

PrismLab 连接你已经部署或购买的模型服务，不负责部署模型。通过 Web 控制台进行交互测试、管理可重复运行的测试计划，也可以使用 CLI 进行轻量并发压测。

## 可以测试什么

| 测试类型 | 测试内容 | 主要指标 |
| --- | --- | --- |
| LLM | 流式文本生成 | TTFT、TPOT、tokens/s、请求耗时 |
| Embedding | 文本向量化 | 延迟、QPS、成功率 |
| Reranker | 问题与候选文档重排 | 延迟、QPS、成功率 |
| Audio | 语音转文字 | 延迟、QPS；已知音频时长时提供 RTF |
| Dify Retrieve | 知识库检索 | 延迟、QPS、成功率 |
| Dify Chat | 应用流式回复 | 首消息耗时、总耗时、成功率 |

控制台支持模型搜索与类型筛选、YAML 驱动的 LLM 参数、定时测试计划、运行记录、性能对比图表，以及手动生成的 AI 测试报告。界面支持中英文与浅色 / 深色主题。

- **统一登录**：本地密码、GitHub OAuth，以及 GitLab 或其他 OIDC 身份提供方。
- **空间隔离**：独立个人空间与共享团队空间，支持 Owner、管理员、普通成员三级权限。
- **团队协作**：指定账号邀请、空间切换、自定义昵称与头像。
- **数据加载**：列表分页、首页数据独立加载，Docker 部署支持响应压缩。

Web 应用使用 **PostgreSQL**，SQLite 仅可作为一次性迁移工具的数据源。Docker Compose 内置 PostgreSQL 16，无需 Redis。

内置供应商预设包括 Moonshot（月之暗面 / Kimi）、千问、MiniMax、火山方舟、DeepSeek、硅基流动、Xinference、vLLM、阿里云百炼、讯飞星辰 MaaS 和华为云。千问作为独立供应商提供，不改变已有百炼配置。可用测试类型及模型列表获取能力取决于供应商与实际部署，并非所有供应商都支持全部类型。配置说明见[新增云供应商](docs/model-discovery.md#新增云供应商)。

## 快速开始

部署使用选择 **Docker**；修改代码选择 **本地开发**。实际测试还需要一个可访问的模型服务或 Dify 实例。

### 方式一：Docker 部署

在仓库根目录复制 `docker/.env.example` 为 `docker/.env`，按 [Docker 部署指南](docker/README.md) 配置账号和数据库，然后启动服务。

```bash
docker compose --env-file docker/.env -f docker/compose.yaml up --build -d
```

打开 [http://localhost:8080](http://localhost:8080)，登录并选择空间。生产部署、离线打包与备份见 [Docker 部署指南](docker/README.md)。

### 方式二：本地开发

安装 [依赖管理指南](docs/dependencies.md) 所列工具并准备 [PostgreSQL](docs/postgresql.md)。从仓库根目录启动后端。

```bash
uv sync --locked
export DATABASE_URL=postgresql://prismlab:prismlab@127.0.0.1:5432/prismlab
uv run --no-sync python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

另开终端启动前端。

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm run dev --host 127.0.0.1
```

打开 [http://localhost:5173](http://localhost:5173)。Vite 将 API 请求转发到 8000 端口。

## 账号与空间

启用应用登录后，每个账号有独立个人空间，也可加入团队。模型配置、测试计划和评测数据均属于当前空间。参见 [登录与 SSO](docs/authentication.md) 和 [个人空间、团队与权限](docs/tenancy.md)。

## 完成第一次测试

1. **模型供应商**：选择内置供应商，添加模型并配置服务地址和凭据，或在支持的供应商中获取并选择模型，然后保存。供应商与模型右侧的小开关分别控制启停：关闭后不再供新任务选择或调用，配置和密钥会保留；供应商重新开启时，各模型恢复各自原来的启用状态。仅空间所有者和管理员可修改。
2. **Playground**：选择测试类型和已配置的模型，发送一次请求检查配置。它会调用真实服务，但不会创建测试计划或运行记录。
3. **测试计划**：填写测试输入，选择模型、并发档位和请求数。建议从并发 1、请求数 5 开始。新建计划保存后不会立即运行，由你决定何时启动。
4. **运行记录**：查看进度、失败情况和测试结果。每次执行都会产生一条独立记录。
5. **分析看板**：比较不同并发档位，或同一计划的多次运行。使用相近的输入与生成参数，对比才有意义。

测试 Dify 时，先进入 **Dify 配置**。服务地址默认是 `https://api.dify.ai`，自部署实例请替换。知识库检索需要知识库 ID 和 Dataset API Key；聊天应用使用独立的应用 API Key。名称旁的刷新按钮可以获取对应名称，但不会自动保存。测试计划与 Playground 会复用配置中的知识库 ID，无需重复填写。

**测试会调用真实 API，可能产生费用。** 先验证单次请求，再提高并发或开启定时重复运行。

## LLM Playground 与 AI 报告

Playground 支持单次流式调试、独立系统提示词和按模型显示的参数。思考能力、参数映射和服务默认值见 [LLM 模型配置](docs/llm-model-configuration.md)。

在「空间设置 → AI 测试报告」设置分析模型和思考模式，再打开已结束的运行记录生成报告。报告基于性能统计并保存在 PostgreSQL。重新分析失败时保留上一次报告。

## RAG 评测数据集

从文档或 Dify 知识库生成样本，也可导入、编辑和导出数据。数据集可用于评测 LLM、Dify Chat 的 RAG 回答，或 Dify 知识库的检索结果。参见 [数据集指南](docs/rag-datasets.md) 和 [质量评测指南](docs/quality-evaluations.md)。

## 命令行测试

CLI 从进程环境和可选的 `cli/.env` 读取服务配置。配置端点和密钥后，从仓库根目录运行。

```bash
uv sync --locked --only-group cli
uv run --no-sync python -m cli.perf_llm --provider deepseek -c 1 -n 5 --json-report output/llm.json
```

其他能力、参数、JSON 输出及环境变量见 [CLI 配置参考](docs/cli-reference.md)。

## 如何理解指标

| 指标 | 含义 |
| --- | --- |
| P50 / P95 / P99 | 延迟分位数：50% / 95% / 99% 的有效成功请求在该时间内完成 |
| QPS | 吞吐量；JSON 中的 `qps_success` 是成功请求数除以总运行时间 |
| TTFT | 客户端发起请求到首段生成内容到达的时间，包含思考内容 |
| TPOT | `（整次请求耗时 − TTFT）÷（输出 token 数 − 1）` |
| tokens/s | `1000 ÷ TPOT`，不包含等待首 token 的时间 |
| 音频 RTF | 请求耗时除以音频时长，越低越快 |

LLM 的 TPOT 与生成速度依赖服务返回的输出 token 用量；缺失用量或 token 不足时显示不可用，不把流式分块数当作 token 数。界面数值显示整数，报告保留统计精度。

Web 控制台、CLI 终端摘要和导出 JSON 均只使用成功请求计算延迟，并以成功请求数除以总运行时间计算 QPS。

这些是 **客户端端到端测量**，包含网络开销，不等于模型的纯推理耗时。少量请求适合验证连接，不适合判断稳定的 P95 / P99；不同运行的分位数不能直接取平均。

## 数据与安全

保存的凭据使用 Fernet 加密。部署和备份时一并保留 PostgreSQL、数据目录及外部配置的密钥。详见 [PostgreSQL 和备份](docs/postgresql.md) 与 [登录和会话](docs/authentication.md)。

## 开发与文档

从 [文档索引](docs/README.md) 查找使用、部署与开发说明。[项目目录与运行入口](docs/repository-layout.md) 和 [依赖管理](docs/dependencies.md) 包含开发和测试命令。未完成的工作见 [路线图](ROADMAP.md)。

## 许可证

[MIT](LICENSE)

# M0：评测基础与快照

> 本文保留 M0 的设计与部署基线。当前执行、双引擎、治理和复杂场景见 [M1–M5 质量评测说明](quality-evaluations.md)。

M0 提供配置、版本、证据、依赖检查和内部 worker 状态契约。**当前没有评测执行器或任务调度器**：创建运行返回 `prepared`，不发出应用或裁判请求。DeepEval 实际评分属于 M1，RAGAS 官方指标属于 M2；评测操作及逐条结果页面属于 M1，版本管理页面属于 M3。

## 配置与引擎

空间设置 `/api/dataset-settings` 新增 `default_judge_llm`、`scoring_engine`、`judge_prompt`、`default_judge_prompt`。生成模型、初始问题质量评审模型和实际回答评分模型分别配置。省略字段保留原配置；显式 `null` 清空模型或恢复默认提示词。模型配置仍只有空间 owner/admin 可以修改。

生成引擎不决定评分引擎：可用 DeepEval 生成、Dify 返回回答、RAGAS 评分契约。被测目标单独选择 `llm`、`dify-chat` 或 `dify-retrieve`。M0 的后两者只冻结连接身份，不执行调用。

`generation_engine` 是可信的身份字段。DeepEval 使用官方 4.3.2 Synthesizer；RAGAS 为 `prismlab.ragas-compatible`，`adapter_version=single-turn-0.4`、`official_sdk=false`、`sdk_version=null`。旧 `sdk`/`sdk_version` 为兼容历史客户端保留，不可将其 RAGAS 版本解释成官方 SDK 版本。新生成记录同时保存安全的生成/评审模型配置；旧记录缺失的信息保持缺失，不反推或伪造历史模型版本。

## API

所有接口沿用空间认证、CSRF 和 `X-Workspace-Id`。基础认证部署沿用已有访问方式。

| 接口 | 用途 |
| --- | --- |
| `GET /api/evaluation-capabilities` | 指标目录、能力、状态枚举；`execution_available=false` |
| `POST /api/evaluation-runs` | 原子冻结数据集并准备运行；不调用模型 |
| `GET /api/evaluation-runs?limit=50` | 当前空间最新运行摘要，最多 100 |
| `GET /api/evaluation-runs/{id}` | 冻结配置、逐样本状态、指标依赖和结果 |
| `GET /api/dataset-versions/{id}` | 不可变样本、来源及生成配置 |
| `POST /api/evaluation-runs/{id}/cancel` | 停止运行并作废 worker 令牌 |
| `POST /api/evaluation-runs/{id}/retry-failed` | 将可重试失败项恢复为 prepared；M0 不会自动执行 |

准备请求示例（替换为当前空间的配置 ID）：

```json
{
  "dataset_id": "local-dataset-id",
  "idempotency_key": "quality-check-001",
  "target": {
    "benchmark": "dify-chat",
    "selection": {"id": "dify-config-id", "model": "app-model"},
    "prompt": "",
    "max_tokens": 1024
  },
  "scoring_engine": "deepeval",
  "judge": {"id": "provider-id", "model": "judge-model", "credential_id": "credential-id"},
  "judge_prompt": "根据证据评分，说明理由，不补造缺失的参考答案或召回内容。",
  "metrics": [
    {"id": "deepeval.answer_relevancy", "threshold": 0.5},
    {"id": "deepeval.faithfulness", "threshold": 0.5}
  ]
}
```

`judge`、`judge_prompt` 和 `scoring_engine` 可省略以使用空间默认配置；没有默认裁判时必须选择。不能混选不同评分引擎的指标。相同空间、相同幂等键和请求返回同一运行，即便默认配置或原数据集随后改变/删除；同一键提交不同请求返回 409。键不会自动过期。并发提交由事务锁和唯一索引保护。

## 不可变证据

新增 `dataset_versions`、`evaluation_runs`、`evaluation_cases`、`metric_results` 四张表，均位于对应空间 schema。创建版本与运行在一个事务内完成，锁定父数据集，保证样本和来源一致；构建中或没有启用样本的数据集不能准备评测。归档样本不参与本次版本。

版本保留启用样本、原参考答案、原上下文、历史导入的输出、来源、生成配置和已知引擎/模型信息；运行冻结目标、裁判、双方提示词、指标定义和阈值。版本包含 SHA-256 `content_hash`，运行记录对应版本和摘要。数据库触发器禁止更新/删除版本，禁止改写运行配置证据。修改、重建或删除原数据集及修改空间设置不影响版本/运行。

原 Golden 的 `actual_output`、`retrieval_context` 仅作为导入的历史字段保留，**本次运行 observation 从空值开始**，不得拿原参考资料或旧回答当本次应用返回结果。`local_dataset_id` 为 PrismLab ID；目标中的 `external_dify_dataset_id`、`external_dify_app_id` 为外部 ID。

解析后的模型连接采用白名单：供应商、配置/凭据 ID、实际模型名、可用的模型 revision、去掉用户信息/查询串/片段的 URL。不保存密钥、加密密钥、请求头、环境变量名或凭据列表；来源/元数据移除凭据结构并清理 URL。样本正文、参考答案、资料和用户提示词保留原文，用户主动放入这些正文的敏感内容仍属于数据内容，须按空间数据权限管理。模型未暴露 revision 时记录 `null`，不宣称能够重现供应商内部模型权重。未来执行器必须重新取得凭据并检查配置变化，不能将修改后的端点冒充冻结目标。

## 指标契约与缺失字段

目录声明实现名称、实现/契约版本、必需字段、模型依赖、分数范围、阈值方向和用量能力。M0 的实现版本统一为 `unverified-contract-only`，所有 `executor_available=false`；实际适配器交付时必须锁定、验证 SDK 与实现版本，不能把本阶段的契约记录当成 SDK 实测结果。M0 的准备记录保留为配置证据；真实评分应在接入执行器后按新的已验证实现创建运行，不能改写旧运行的实现版本。

| 契约 ID | 必需数据 |
| --- | --- |
| `deepeval.answer_relevancy` | input、actual_output |
| `deepeval.faithfulness` | actual_output、retrieval_context |
| `deepeval.reference_correctness` | input、expected_output、actual_output；预留业务正确性实现契约 |
| `ragas.faithfulness` | input、actual_output、retrieval_context |
| `ragas.context_recall` | input、expected_output、retrieval_context；LLM recall 变体 |

本批契约范围为 `[0,1]`，比较操作 `gte`，默认阈值 0.5；这是可修改的起始配置，不代表校准后的业务标准。不同框架分数不可混合解释。目录的 requests/tokens 为待执行器或供应商提供；缺定价的成本未知，不写零。

准备时：没有参考答案为 `missing_data`；实际回答/召回尚未调用为 `awaiting_target`。目标返回后缺少实际召回时仍为 `missing_data`，参考 `context` 不满足实际 `retrieval_context` 依赖。空白字符串、空数组或含空白片段的数组视为缺失。依赖满足可显示 `data_status=ready`，但执行能力仍为 false。缺字段、目标失败或裁判失败的 `score`、`passed` 均为空，不能计作零分/通过。

## 状态与 worker 契约

| 层级 | 状态及含义 |
| --- | --- |
| Run | prepared → running → completed / partial_failed / failed；prepared/running 可取消至 cancelled |
| Case | pending → completed / target_error；未提交目标结果时可取消至 cancelled |
| Metric | pending → completed / judge_error / missing_data；未评分时可取消至 cancelled |

`completed` 表示执行完成，不表示所有质量阈值通过。全部指标完成为 completed；仅部分可评分成功为 partial_failed；无评分成功为 failed。目标调用错误只写通用 `target_call_failed`，裁判调用错误写 `judge_call_failed`，不存可能带凭据的原始异常文本。缺字段原因保存在 dependency 中。

`EvaluationStore.start/record_target/record_metric` 是内部接口，不提供外部 HTTP 结果写入或启动接口。每轮 start 产生独立 token；所有结果写入锁定所属运行并核验 token、case/result ID 和状态。重复结果提交返回冲突，不能覆盖第一次证据；创建请求幂等。cancel 幂等，已完成证据保留，未完成项取消；迟到/旧 token 的写入返回冲突。取消后的运行不接受失败重试，应以新幂等键重新准备。

失败重试只重置 target_error 或 judge_error 及其受阻指标；成功的结果不重跑，失败记录追加到 history，冻结配置不变。仅因不可变参考数据缺失而失败不能重试，应修复原数据后创建新版本。服务重启将 running 标为 failed/server_restarted，保留已完成结果，将未完成项转为可解释、可重试的错误；prepared 保持 prepared。M0 可验证这些内部转换，但不会调度 worker。

后台 worker 必须在请求内捕获具体 `EvaluationStore`，离开请求后不能依赖环境中的 WorkspaceProxy/ContextVar。跨空间 ID 返回 404，无成员权限的指定空间返回 403；内部 worker 同样只访问捕获 schema。

## 验证和兼容性

自动化覆盖：不可变版本和数据库保护、原数据/来源/设置变更与删除、凭据剥离、缺参考/实际召回、幂等并发、重复提交、取消/重试/旧 token、重启恢复、成员配置权限、跨空间结果/版本/worker 隔离。继续运行现有数据集、PostgreSQL 和性能 runner 回归。

数据库升级只新增四表、索引和保护触发器，在 public 及现有/新建空间 schema 执行；不改写旧数据集与性能记录。回退应用镜像可保留新增表；备份恢复为完整灾难恢复手段。M0 不自动建立旧数据集版本、不请求外部模型、不改写历史性能响应保留策略。

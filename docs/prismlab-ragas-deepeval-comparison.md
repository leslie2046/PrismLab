# PrismLab、RAGAS 与 DeepEval：能力对比及功能建议

后续实施阶段、依赖、任务编号与验收标准见 [产品路线图](../ROADMAP.md)。

本报告保留 M0 实施前的比较基线。2026-11-07 M0 已新增模型角色分离、不可变评测快照、指标依赖及运行状态契约，详见 [M0 实施说明](evaluation-foundations.md)；实际应用调用与评分执行仍待 M1/M2。

核对日期：2026-11-07。PrismLab 以当前工作区的实际实现为准；外部框架以本次查阅的官方文档及 PyPI 为准。此次查询到 RAGAS 0.4.3、DeepEval 4.3.3；项目当前锁定 DeepEval 4.3.2，未安装官方 RAGAS SDK。官方在线文档可能覆盖比项目锁定版本更新的能力，不能据此认为项目已经接入。

本报告区分开源 SDK 与配套商业平台。DeepEval 开源 SDK 的能力不等于 Confident AI 平台全部能力，托管监控、团队协作等不默认属于免费、自托管 SDK。本报告没有对三者开展同数据集实测，下面比较的是功能与接入情况，不是准确率或速度排行榜。

## 1. 结论

PrismLab 已有性能测试、模型和 Dify 配置、数据集管理、多租户权限与网页操作基础。当前质量评测尚未形成闭环：数据集不能直接驱动批量应用调用、逐条质量打分、版本回归与质量看板。

已有两种生成路径的接入深度不同：

- DeepEval：实际使用官方 Synthesizer、Golden、质量过滤、问题演化和风格配置；尚未接入质量评测执行器和指标。
- 当前 RAGAS 选项：项目自己的单轮样本格式兼容生成器。通过已有模型接口生成 `user_input/reference/reference_contexts`，再映射为 Golden；没有调用官方 TestsetGenerator，也没有接入 RAGAS 评测指标。`single-turn-0.4` 是项目适配器标识，不是官方 RAGAS SDK 版本。

建议定位：以 PrismLab 提供产品界面、数据、权限、运行管理与结果对比，以 RAGAS/DeepEval 提供可选评测和生成算法。优先补齐「数据集 → 调用目标应用 → 收集回答与实际召回 → 指标评分 → 查看差异」这一流程。

## 2. 产品与工程能力

表中的“未接入”指 PrismLab 产品没有开放该能力；底层依赖包中存在相应类，不计为产品已经支持。

| 比较项 | PrismLab 当前实现 | RAGAS 官方开源能力 | DeepEval 官方开源能力 |
| --- | --- | --- | --- |
| 核心定位 | 网页化性能测试与数据集管理平台，质量评测待接入 | LLM/RAG 评测、实验与测试数据生成框架 | LLM 应用测试框架，覆盖端到端、组件及轨迹评测 |
| 使用方式 | 网页、HTTP API、性能测试 CLI | Python/实验代码，需要产品集成 | Python、CLI、pytest，需要产品集成 |
| 自有模型和凭据 | 多供应商、模型发现、加密密钥、空间隔离 | 可配置 LLM/embedding 与自定义适配 | 可配置评审模型、自定义 LLM/embedding |
| Dify | 知识库列表、同步分段、聊天应用手动接入、检索/聊天性能测试 | 可由代码接入应用和评测样本；不是本项目已有的 Dify 管理界面 | 可由代码接入应用和测试用例；不是本项目已有的 Dify 管理界面 |
| 性能压测 | LLM、embedding、reranker、音频、Dify；并发场景和吞吐量 | 重点是评测任务执行、并行与开销管理 | 重点是评测任务执行、并行与开销管理 |
| 性能指标 | 成功率、QPS、延迟分位数；LLM 支持 TTFT/TPOT/tokens/s，取决于接口返回用量 | 不能直接替代本项目的完整性能压测流程 | 执行开销/追踪能力不能直接替代本项目的并发压测流程 |
| 历史与计划 | 性能运行历史、定时计划、性能对比、AI 性能报告 | 有实验与结果组织能力，业务平台需集成 | 有测试运行结果与 CI 集成；云端产品另计 |
| 网页权限与租户 | 个人/团队空间、Owner/管理员/成员、SSO、PostgreSQL schema 隔离 | SDK 不直接提供本项目整套租户管理 | SDK 不直接提供本项目整套租户管理；Confident AI 另有平台能力 |
| 私有部署 | Docker Compose，模型请求到配置端点 | 可在自有运行环境使用；模型调用是否外发由配置决定 | 可本地/自托管执行；是否连接云平台及模型端点由集成配置决定 |
| 开源许可证 | MIT | Apache-2.0 | Apache-2.0；商业平台条款另计 |

性能测试用的调用并发，与评测裁判请求的并发需要分开配置。裁判服务被限流不应被计为被测应用的服务失败。

## 3. 数据集生成与管理

| 比较项 | PrismLab 当前实现 | RAGAS 官方能力 | DeepEval 官方能力 |
| --- | --- | --- | --- |
| 文档/上下文生成 | 支持 TXT/Markdown/PDF/DOCX/CSV、Dify 分段；PDF 扫描件需 OCR 后导入 | 支持文档到测试集，结合 LLM、embedding 和图变换 | Synthesizer 支持文档、预处理上下文生成 |
| 原生框架接入 | DeepEval 已接；RAGAS 仅格式兼容 | TestsetGenerator | Synthesizer |
| 文档处理深度 | 简单字符切分；按位置分散抽取相邻片段 | 知识图谱变换、关系、摘要等生成路径 | 官方 docs 路径支持上下文构建/选择；本项目当前只使用已准备的 contexts 路径 |
| 单跳/多跳/跨文档覆盖 | DeepEval 有问题演化；没有显式类型配额及覆盖率看板；当前 RAGAS 无原生多跳生成策略 | 单跳、多跳与 query distribution；知识图谱辅助组织上下文 | 问题演化、多上下文等能力；具体方法依 SDK 版本与生成路径 |
| 用户角色和任务分布 | 自然语言“应用场景”，没有结构化 persona 和比例配置 | Persona、查询类型与分布可定制 | 任务、场景、输入/输出风格可定制 |
| 从种子扩写 | 两条路径均支持，保留来源；RAGAS 是项目提示词适配 | 可自定义生成器与知识图谱生成策略，不把项目扩写接口视作官方同名原生接口 | 支持 Golden 扩写 |
| 无资料生成问题 | 支持按任务生成，不生成参考答案 | 官方主线偏知识驱动的测试集生成，自定义场景需选择合适生成器 | 支持从 scratch 生成 Golden |
| 问题质量过滤 | DeepEval 初始问题评审、阈值与重写；当前 RAGAS 无此过程 | 有图转换和生成流程，可扩展过滤；不等同 DeepEval FiltrationConfig | FiltrationConfig；达到重试上限后不等于所有样本都通过人工质量要求 |
| 最终答案事实审查 | 尚未实现；生成评审只检查初始问题 | 可组合 Faithfulness/FactualCorrectness 等指标另行评测 | 可组合 Faithfulness/自定义正确性指标另行评测 |
| 自然语言配置易用性 | 任务/场景/问题/答案风格，示例点击填入、说明提示、按条件隐藏 | 通过生成器、persona 和提示词定制；需产品封装 | StylingConfig 等参数；本项目已封装为表单 |
| 去重 | 规范化空白和大小写后的问题精确去重；不是语义去重 | 生成策略与数据处理可扩展；需验证业务级语义去重要求 | 演化/过滤不等于业务级语义去重，仍需产品策略 |
| 生命周期管理 | 导入、手工编辑、归档、多选、批量导出、追加生成、失败续建 | 数据结构/生成/实验基础；团队操作流程需产品集成 | Golden/EvaluationDataset 基础；本地 SDK 与云端管理功能需区分 |
| 安全重新构建 | 成功后替换可识别 AI 记录；保留手工/导入/编辑数据，失败保留原数据 | 需要宿主应用定义业务事务与记录保留策略 | 需要宿主应用定义业务事务与记录保留策略 |
| 人工审核 | 可编辑/评论字段与启用归档；没有分配、审批、审核队列及审计流 | 可与人工标注对齐，业务审核界面需集成 | 可与人工判断配合；完整平台审核流程另计 |
| 版本与复现 | 有参数、提示词、来源快照；没有不可变数据集版本和质量实验基线 | 可组织数据/实验；产品不可变版本策略需明确 | 支持测试数据和回归；云端版本/协作能力另计 |
| 用量与预算 | 部分输出 token、调用次数；生成费用为未知，没有完整预算限额 | 支持用量/成本相关回调等，需接入和价格定义 | 支持执行与评审开销管理，完整预算产品逻辑需集成 |

特别注意：`quality_threshold` 当前是“初始问题质量”的阈值。它不能作为最终参考答案事实正确性的保证，也不能等同于未来应用评测中的正确率阈值。

## 4. 评测能力

| 比较项 | PrismLab 当前实现 | RAGAS 官方能力 | DeepEval 官方能力 |
| --- | --- | --- | --- |
| 用数据集批量调用被测系统 | 未接入；性能计划输入目前与本地 Golden 数据集没有打通 | 可将应用输出组装成 EvaluationDataset/实验行进行评测 | 可将应用输出组装成测试用例进行 evaluate/assert_test |
| 保留实际回答和实际召回 | Golden 能容纳相关字段，但没有批量采集质量结果流程；性能报告有意不保存响应正文 | 评测样本包含 response/retrieved_contexts 等 | 测试用例包含 actual_output/retrieval_context 等 |
| 回答忠实度/依据性 | 未接入 | Faithfulness 等 | Faithfulness 等 |
| 回答相关性 | 未接入 | Response Relevancy 等 | Answer Relevancy 等 |
| 事实/参考答案正确性 | 未接入 | FactualCorrectness、相关正确性评测方法 | G-Eval 等自定义正确性标准 |
| 检索准确性与召回 | 目前只有检索性能，没有质量评分 | Context Precision/Recall，存在 LLM、非 LLM、ID 等不同实现 | Contextual Precision/Recall/Relevancy 等 |
| 自定义业务规则 | 可填写生成要求，但没有业务规则评分器 | 自定义指标、Aspect Critic、rubric 等 | G-Eval、DAG、自定义指标 |
| 无标准答案评测 | 尚未接入；生成时可以不存答案 | 部分指标可不用标准答案，取决于指标所需字段 | 部分指标可不用 expected_output，取决于指标所需字段 |
| 多轮对话 | 未接入；当前数据集是单轮，Playground 也是单请求 | 多轮样本及相应指标/集成 | ConversationalGolden、ConversationalTestCase、模拟和多轮指标 |
| Agent/工具轨迹 | 仅保留 Golden 工具字段，不代表执行和评分 | 目标完成、工具调用准确性等指标 | 端到端、轨迹、组件、工具使用等评测 |
| 多模态质量 | 音频目前测性能，没有文本/图像/音频质量评测流程 | 有特定多模态指标，覆盖范围依指标与版本 | 有多模态和语音 Agent 指标，覆盖范围依指标与版本 |
| 裁判与人工一致性 | 未接入 | 有与专家标注对齐的官方工作流 | 可自定义标准、rubric 和人工校验；不能假定与 RAGAS 校准流程相同 |
| CI 发布门禁 | 没有模型质量回归命令或质量阈值退出码 | 可脚本化评测和自建门禁 | pytest/assert_test、deepeval test run、CI/CD 文档 |
| 线上追踪与质量监控 | 有测试任务进度、性能历史；无应用调用链质量监控 | 可接入实验和外部追踪体系 | 有追踪/插桩集成；托管监控、协作能力需区分 Confident AI |

RAGAS 的 Context Recall 与基于相关片段 ID 的 Recall@K 不是同一指标；DeepEval 与 RAGAS 的同类指标也不能只因为分数都在 0–1 就混合平均或直接互设同一阈值。指标定义、版本、所需字段、评分模型和提示词都应保存。

## 5. 建议优先建设的功能

P0：下一阶段的核心交付。P1：完成质量评测后扩展。P2：按真实业务需求建设。

| 优先级 | 建议功能 | 解决的问题 | 建议最小交付与验收 |
| --- | --- | --- | --- |
| P0 | 数据集批量评测 | 现在的数据集生成后不能直接验证应用效果 | 选择数据集版本、Dify Chat/Retrieve 或 LLM 目标，批量运行；逐条查看成功/失败、实际回答、实际召回、耗时；支持取消和失败重试 |
| P0 | 质量指标及明细 | 现在只有性能指标，没有回答/检索质量证据 | 首批提供忠实度、回答相关性、正确性和检索指标；逐条分数、理由、阈值、不可计算原因；不把评分失败当作零分 |
| P0 | 指标输入检查和模板 | 用户不知道无答案/无召回时应该选什么 | 预置“仅检索”“无标准答案问答”“有标准答案问答”；自动检查所需字段，说明哪些指标可算、哪些缺数据 |
| P0 | 数据集版本与运行快照 | 同一数据集改动后无法公平对比 | 显式发布不可变版本；冻结样本、资料、被测配置、评测配置、模型/SDK、指标定义；历史结果不被编辑覆盖 |
| P0 | 评测角色与费用控制 | 把生成评审、应用调用、结果裁判混为一谈，费用难控 | 区分生成模型、生成质量评审模型、结果评分模型；先试跑少量样本；显示用量、预算、超限停止；模型余额/认证错误立即停止，限流退避 |
| P0 | 引擎身份与能力说明 | 当前兼容模式容易让用户误以为拥有官方 RAGAS 全部能力 | 显示“RAGAS 兼容生成”及真实适配器版本；生成引擎与评分引擎分别选择；所有参数从实际能力决定显示 |
| P1 | 原生 RAGAS 生成后端 | 当前缺少其知识图谱、多跳、persona 和类型分布优势 | 接入 TestsetGenerator、embedding 模型、图变换和查询分布；保留现有兼容模式或制定迁移策略；记录真实 SDK 版本 |
| P1 | 对比实验 | 不知道更换模型、提示词或检索设置是否改善 | 同一不可变数据集版本对比 A/B；结果并排、逐条差异、分类切片；同时显示质量、P95 与成本，支持基线 |
| P1 | 人工审核与裁判校准 | 合成答案和自动评分仍可能出错 | 待审核/通过/驳回、审核人、理由；引用资料并排查看；用独立人工标注集验证评分模型的一致性 |
| P1 | 数据覆盖率与语义去重 | 只知道生成了几条，不知道测到了哪些内容 | 按文档、章节、主题、问题类型统计；预览语义重复候选供合并；支持配额和覆盖不足提示 |
| P1 | 负例、拒答、澄清样本 | 只测能回答的问题，漏掉知识边界与不完整提问 | 增加“应回答/应拒答/应澄清”标签；配置比例并单独评测；“不生成参考答案”不等于“该问题应该拒答” |
| P1 | CI 回归门禁 | 改动后可能出现质量下降但无法自动拦截 | 通过 API/CLI 指定版本及基线，输出 JSON/HTML、机器可读状态和退出码；明确质量不合格与系统执行故障的差别 |
| P2 | 多轮对话和 Agent 轨迹 | 无法测试追问、记忆、工具使用、任务完成 | 独立对话/轨迹数据结构与采集器，接入相应指标，不把多轮塞成单轮字符串 |
| P2 | 生产失败回流与安全评测 | 离线集可能遗漏真实问题 | 选择性导入线上失败样本、去标识和审核后入库；按业务需要集成安全/红队能力，并明确是否属于独立生态项目 |
| P2 | 持久任务队列与多 worker | 当前调度和任务状态依赖单进程，长任务需更稳健运行 | 持久任务、租约、取消、幂等提交、分批恢复；按实际规模决定是否引入任务队列服务 |

平台还可改进同一供应商下多个模型的同时对比：当前 RunCreate 的唯一性只按 provider 配置 ID 校验，同一配置下的不同模型不能作为两个目标同时选择。后续应按“配置 ID + 模型 + 凭据”定义目标身份。

## 6. 首批指标与数据要求

以下是产品应提供的模板，不代表项目已经实现。具体选用的 SDK 类须按锁定版本验证其 required columns。

| 产品指标 | 回答的问题 | 最少需要哪些数据 | 是否需要标准答案 | 建议说明 |
| --- | --- | --- | --- | --- |
| 忠实度/依据性 | 实际回答是否能从实际检索上下文得到支持 | 实际回答、实际检索上下文；部分实现还需问题 | 通常不需要 | 不能用数据集原始资料冒充本次实际召回 |
| 回答相关性 | 实际回答是否围绕问题 | 问题、实际回答 | 通常不需要 | 相关不代表事实正确；某些实现另需 embedding/LLM |
| 参考答案正确性 | 实际回答与经过审核的正确事实是否一致 | 问题、实际回答、参考答案 | 需要 | 合成参考答案须先抽样审核；不做简单字符串完全匹配 |
| 检索 Context Precision | 排名前列的召回内容是否有用 | 问题、实际召回；根据实现还需参考答案或实际回答 | 视实现而定 | 为不同变体明确标出依赖，不给一个含糊的万能开关 |
| 检索 Context Recall | 需要的事实/内容是否被召回 | 实际召回，以及参考答案、参考上下文或相关性标注 | 需要某种参考依据 | 无参考信息时应显示不可计算，不自动填零 |
| Recall@K / MRR / nDCG | 相关片段是否被召回及排序是否合理 | 实际召回片段 ID、标注相关片段 ID；nDCG 可需分级相关性 | 不一定需要文字答案，但需要相关性标注 | 优先用于检索链路对比；保留稳定 chunk/document ID |
| 自定义业务标准 | 术语、格式、合规要求和流程是否满足 | 实际回答、明确评分规则；按规则补充问题/参考资料 | 视规则而定 | 可用 G-Eval/自定义指标；确定性格式校验优先直接计算 |
| 拒答/澄清正确性 | 应该拒答或追问时是否采取正确行为 | 问题、期望行为标签、实际回答，必要时资料 | 需要行为标注 | 将“缺少参考答案”与“期望拒答”分开 |

不应一开始把所有指标都默认打开：先根据数据字段选择模板，再显示费用和依赖。对已经拥有审核参考答案的知识库问答，先启用正确性、忠实度、相关性；对仅检索测试，优先使用有相关性标注的检索指标。每次评分保留实际使用的上下文、裁判版本与理由。

## 7. 推荐实施顺序与结构

1. 第一阶段：打通现有数据集到 Dify Chat/Retrieve 的批量评测；完成实际输出采集、首批指标、可计算性检查、逐条结果、取消/重试和费用预览。先使用已引入的 DeepEval 评测接口降低起步改动，再通过统一适配接口接入 RAGAS 指标；也可按具体业务首批指标选择 RAGAS，接口层不绑定某一家。
2. 第二阶段：数据集版本与基线完善、A/B 对比、人工审核、覆盖率；接入官方 RAGAS TestsetGenerator，增加 embedding 模型配置和多跳/角色分布。跨阶段的首次质量运行从一开始就必须保存不可变样本与配置快照，不能等版本 UI 完成后才追溯。
3. 第三阶段：CI 门禁、生产问题回流、必要的多轮/Agent 和持久任务运行架构。

建议逻辑结构：

```text
数据集版本
    → 运行目标（Dify Chat / Dify Retrieve / LLM / 后续通用 HTTP）
    → 逐条采集结果（回答、实际召回、片段 ID、耗时、运行状态）
    → 指标执行器（DeepEval / RAGAS / 确定性规则）
    → 指标明细（分数、理由、版本、费用、错误/不可计算原因）
    → 对比实验、人工复核、CI 门禁
```

生成引擎、被测目标、评分引擎是三个独立选择。例如：使用 DeepEval 生成数据，测试 Dify 应用，再使用 RAGAS 评分，流程应当成立。同一指标固定实现、模型和版本后才能公平对比；不同引擎同名指标并列呈现，先用人工标注校准再考虑业务阈值。

质量评测结果应独立持久化，避免直接修改共享 Golden 的 `actual_output` 作为唯一历史：同一条 Golden 可能被多个应用和版本同时测试。也应保留目前性能日志不保存正文的设计，把确需保存的质量样本放进受空间权限控制、保留策略明确的质量结果存储。

建议最小实体为 `dataset_versions`、`evaluation_runs`、`evaluation_cases`、`metric_results`，名称仅为设计建议；同时将本地评测数据集 ID 与外部 Dify knowledge base ID 明确区分，避免复用含义不同的 `dataset_id`。

## 8. 项目核查依据

- `pyproject.toml`：项目依赖固定 `deepeval==4.3.2`，无 `ragas` 运行时依赖。
- `backend/deepeval_generation.py`：官方 Synthesizer、FiltrationConfig、EvolutionConfig、StylingConfig；初始问题评审元数据。
- `backend/ragas_generation.py`：兼容适配器、自有请求提示词、字段转换、项目适配器版本。
- `backend/datasets.py`、`backend/dataset_store.py`、`backend/dataset_routes.py`：生成参数、导入、精确去重、来源快照、替换事务、重试与用量；不存在质量评测运行接口。
- `backend/schemas.py`、`backend/runner.py`：性能请求及目标组合，没有本地 Golden 数据集驱动的质量运行。
- `shared/report_writer.py`：性能报告只保留 allowlist 指标，不保存问题与回答正文。
- `backend/ai_reports.py`：只解释性能统计，明确不能据此推断回答质量。
- `docs/rag-datasets.md`、`docs/tenancy.md`：当前数据集范围、空间隔离、权限和部署边界。

## 9. 官方参考资料

本次核对的直接来源：

1. [RAGAS 指标目录](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/)
2. [RAGAS 测试集生成原理](https://docs.ragas.io/en/stable/concepts/test_data_generation/rag/)
3. [RAGAS 知识图谱与 TestsetGenerator 示例](https://docs.ragas.io/en/stable/getstarted/rag_testset_generation/)
4. [RAGAS evaluate API](https://docs.ragas.io/en/stable/references/evaluate/)
5. [RAGAS 实验](https://docs.ragas.io/en/stable/concepts/experimentation/)
6. [RAGAS 与人工评审对齐](https://docs.ragas.io/en/stable/howtos/applications/align-llm-as-judge/)
7. [RAGAS Context Precision](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/)
8. [RAGAS Context Recall](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_recall/)
9. [DeepEval 指标目录](https://deepeval.com/docs/metrics-introduction)
10. [DeepEval Golden Synthesizer](https://deepeval.com/docs/golden-synthesizer)
11. [DeepEval 测试用例](https://deepeval.com/docs/evaluation-test-cases)
12. [DeepEval G-Eval](https://deepeval.com/docs/metrics-llm-evals)
13. [DeepEval CI/CD](https://deepeval.com/docs/evaluation-unit-testing-in-ci-cd)
14. [DeepEval GitHub README（区分开源和 Confident AI）](https://github.com/confident-ai/deepeval)
15. [RAGAS PyPI](https://pypi.org/project/ragas/) · [DeepEval PyPI](https://pypi.org/project/deepeval/)

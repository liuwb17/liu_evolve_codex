# Historical README snapshot — not current instructions

This preserves the pre-handoff README. Status and commands may be obsolete; read the root README first.

# MOSAIC AAD

## v5：针对v4退步的修订与实验

新增单请求thinking代码生成、最多6处协调编辑、交叉配额、实际运行预算诊断、强父代优先及复测失败修复回流。保留重复合法性验证，区分训练探索与最终验证换优。[v5复盘、设计及实验配置](docs/V5_DESIGN.md)。启动前66项测试通过；真实实验成绩以终测报告为准，不预先声称改善。

## v4：已实现，尚未进行真实演化实验

针对v3的截断损耗、初代不足、单次噪声换优和参数接口故障，新增设计/代码分离、精确单编辑、谱系多岛、成本感知算子选择、重复配对换优和保守验证选型。详见 [v4设计与运行指南](docs/V4_DESIGN.md)，入口为 [v4_search.py](scripts/v4_search.py)。测试通过不等于算法性能提升；尚无v4的私有测试成绩。

一个可运行、可审计的自动算法设计 agent，首个任务是 **ALE-Bench 的 AHC001（AtCoder Ad）**。通过分块代码进化、多岛行为档案、算子选择和分级评测，把模型生成的算法转换为可验证的独立求解程序。

研究依据与取舍见 [调研报告](docs/RESEARCH.md)。官方基准接入见 [ALE-Bench 指南](docs/ALE_BENCH.md)。本项目不声称复现 AlphaEvolve/ALE-Agent 的全部机制或达到其性能。

## v3 实验：纯 LLM 冷启动（进行中）

最新检查点：训练 40 例均分 **987.580295M**，40/40 合法、原版 Rust 复核零差异；完成 64 次模型生成请求名额与 64 组本地参数搜索。验证 10 例及私有 1000 例尚未评测，**尚未证明超过 kusano**。当前运行已结束，继续付费调用前等待扩充预算确认，详见 [v3 检查点](docs/V3_CHECKPOINT.md)。

按新的研究要求，初代和后续求解器源码全部由 DeepSeek API 生成，不提供人工算法骨架、旧版解法或人类参赛源码。使用官方 50 个公共实例划分 40 训练/10 选型，执行多初代、多样性档案和分级评测。当前工作目录为 `runs/v3_pro_fromscratch`，实验与失败记录均保留。**下方 v2 的 985.163M 不属于 v3，不能作为此次“从零”实验成绩。**

[实验协议](docs/V3_PROTOCOL.md) · [控制器](scripts/v3_search.py) · [源码来源审计](scripts/v3_audit.py) · [只读进度查询](scripts/v3_status.py)。目标是超过人类对照，但只有冻结后的完整私有测试才能确认是否达成。当前已启用 `--full-evaluation`，短预算仅作合法性检查，合法候选全部进行 40 例完整训练评测，避免短预算排名误淘汰。

新增[第二层数值演化](docs/V3_NUMERIC_LAYER.md)：由模型选择参数接口与范围，本地差分进化搜索组合，再由模型生成受限参数块并进行来源审计。结构代码与参数块均有模型来源；控制器、领域诊断及数值搜索属于人工实现的基础设施。该层的实际效果须以运行记录为准，不能把模拟测试当作算法成绩。

## v0.2：细粒度 C++ 演化

补充人类对照（2026-09-20）：kusano 公开源码在同一 1000 例上均分 **989.413M**，全部 AC、Rust 复核一致。其内置搜索预算为 4.8 秒，与本版 3.5 秒不同，不能作为等搜索预算对比；未将此源码标为冠军提交。详见 [人类基线实测](docs/HUMAN_BASELINES.md)。

**970M 目标已达成：官方 1000 例均分 985.162650M，1000/1000 AC，原始 Rust 计分差异 0。** 采用 3.5 秒搜索预算、容器内候选进程 5 秒限制，最大程序耗时 3.575 秒；Docker 启停另计。这不是官方标准硬件排名。结果与两次失败运行的披露见 [v2 验收](docs/V2_RESULTS.md)，完整证据见 [系统测试报告](runs/v2_system_1000_runtime5/report.json)。

针对 970M 目标，新增联合邻域模拟退火骨架，将参数、几何提案、冲突处理、温度日程和搜索循环分成 5 个可演化块。控制器采用父代池、跨候选启发、编译诊断修复，以及 0.5 秒筛选 → 4.25 秒选型的分级评测。v1 的多岛框架与历史结果仍保留；v2 使用独立控制器，并非所有 v1 机制的叠加。

本轮沿用 DeepSeek Chat Completions 服务。新骨架属于人工工程贡献，模型在其上优化代码，不能将全部提升归因于 LLM。设计、数据隔离与运行协议见 [v2 设计](docs/V2_DESIGN.md)。冻结程序见 [best.cpp](runs/v2_deepseek/best.cpp)，完整模型证据见 `runs/v2_deepseek/`。

```bash
docker build -f docker/Dockerfile.cpp -t mosaic-cpp:1 docker
# 新实验才运行搜索；交付目录已有冻结实验，请勿覆盖。
python scripts/v2_search.py --run-dir runs/v2_new --ask-key --calls 12
# 对交付的冻结候选评测，不调用模型：
python scripts/v2_system_test.py
```

官方测试需要先按 [ALE-Bench 指南](docs/ALE_BENCH.md) 准备原始数据与 Rust 工具。已有评测缓存可续跑，已生成最终报告则不重复执行。交付目录的冻结搜索不能通过 `--resume` 继续调参。

## 快速运行

Python >=3.11；基础项目零第三方依赖。从项目根目录运行：

```bash
python -m aad prepare --count 12
python -m aad run --config configs/offline.json --finalize
python -m unittest discover -s tests -v
```

当前机器可使用 `C:/ProgramData/Anaconda3/envs/opt/python.exe` 替代 `python`。已创建数据时无需再次 prepare。若需要官方题面样例，先取得官方数据包，再传 `--sample data/reference/ahc001/example_input.txt`。

离线模式用有限的人工规则产生真实源码变异，不调用模型，也不伪装成 LLM 实验。实际历史结果与独立可运行的优胜程序保存在 `runs/offline/`。

## DeepSeek 真实进化

需要已运行的 Docker（Linux containers）及有效 API key：

```bash
docker pull python:3.11-slim
python -m aad run --config configs/deepseek.json --ask-key --finalize
```

`--ask-key` 不回显、不落盘；也可预先配置环境变量 `AAD_API_KEY`。配置文件只有服务地址和模型名称。`configs/deepseek.json` 默认最多 6 轮、10 次请求，每次最多 8192 输出 tokens；默认关闭 thinking 以进行成本有限的首轮验收，可通过 `request_options` 调整。费用由实际服务商结算。

支持 Chat Completions 兼容服务：修改 `base_url` 和 `model` 即可。URL 可以包含 `/v1`，但不要带 `/chat/completions`；程序会追加该路径。服务需提供 `/models`、文本 `message.content` 和标准 Chat Completions 返回结构。可选服务参数通过 `request_options` 传入。

真实模型候选默认仅在 Docker/ALE 中执行。`native` 是可信本地测试模式，不是沙箱。不要为了方便在普通工作机上给不可信候选开启 `allow_unsafe_native`。

## 工作流与产物

```text
父代 + 启发程序 + 逐例反馈
             ↓
算子选择 → LLM / 离线规则 → 命名源码块修改
                              ↓
语法 → 小训练集 → 全训练集 → 选型验证集
                    ↓              ↓
              多岛行为档案      最终候选
                                   ↓
                           冻结 → 一次留出评测
```

- `aad/assets/seed.py`：完整初始程序；`parameters`、`priority`、`search` 为演化区。
- `runs/<name>/best.py`：可独立读 stdin、写 stdout 的最终 AHC001 程序。
- `state.json`：候选谱系、档案、算子统计、随机状态和搜索事件。
- `candidates/<sha256>/`：每版源码及评测记录。
- `llm/`：无密钥的请求、响应及调用账本，包括失败请求。
- `report.md` / `report.json`：初始与最终程序的配对留出结果，逐例状态、输出、耗时和 token 使用量。

恢复未冻结的搜索：增加配置中的总 `iterations` / `max_requests`，然后执行：

```bash
python -m aad run --config configs/deepseek.json --resume --ask-key
python -m aad finalize runs/deepseek
```

完成 `finalize` 后拒绝继续搜索，避免将留出成绩用来调参。配置、数据、初始源码变化会使续跑失败。新实验使用新的 `run_dir`；同一目录只允许一个控制进程运行。

## 结果解释

分数统一为原始题目分数除以 1e9。合成样例使用题面分布与 Python RNG，不能视为官方 Rust seed 的复现。验证集参与选型，只有冻结后评测的 holdout 可以报告为本次内部留出结果。

离线验收（单搜索 seed=42，24 轮，13 训练/12 验证/12 留出样例）：

| 程序 | 训练 | 验证 | 留出 |
|---|---:|---:|---:|
| 初始 | 0.818999 | 0.822367 | 0.835370 |
| 最终 | 0.914490 | 0.913769 | 0.918496 |

12 个留出样例全部合法。提升只反映该有限算法族与本次样本；不构成对 AlphaEvolve、ALE-Agent 或其他框架的优势结论。真实模型与官方 ALE-Bench 的实测状态见 [交付验收记录](docs/VALIDATION.md)。

真实 DeepSeek 首轮也已完成：6 轮、9 次模型请求，内部留出均分 **0.835370 → 0.905074**。冻结后另测 50 个官方公共样例，均分 **0.804479 → 0.904774**，全部合法，100 份基线/最终输出与原始 Rust 计分器完全一致。尚未运行官方私有集排名。

项目包：`dist/mosaic-aad-v0.2.0.zip`，包含源码、配置、样例、演化记录及验证结果；不含模型密钥、Docker 镜像或上游编译目录。可通过 `python scripts/package_project.py` 重新打包。旧版 v0.1.0 包保留用于历史追溯。

### 完整官方 1000 例系统测试（2026-09-19）

已下载 AtCoder 官方系统种子，验证官方 MD5，确认与 ALE-Bench 的 1000 个 private seeds 及顺序完全一致。冻结的 DeepSeek 程序取得总分 **903,069,939,479**，归一化均分 **0.903070**，1000/1000 合法；初始基线为 **0.826034**，相对提升 **9.33%**。两份程序共 2000 份输出的独立计分与官方 Rust 计分完全一致。

详见 [1000 例测试报告](runs/system_test_1000/report.md) 和 [逐例比较](runs/system_test_1000/per_case.csv)。输入在 `data/official_system_1000/`。本次为完整官方系统集上的本机 Docker 评测，未计算官方标准环境排名。

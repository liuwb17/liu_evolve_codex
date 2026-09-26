# AAD 调研与设计依据

调研日期：2026-09-17/18。以下区分原工作、工程实现与本项目提出的组合，不宣称新方法已有论文级优势。

## 一手资料与结论

| 工作 | 查阅来源 | 对本项目的启发 |
|---|---|---|
| FunSearch | [Nature 论文](https://www.nature.com/articles/s41586-023-06924-6)；[官方实现](https://github.com/google-deepmind/funsearch) | 可执行程序搜索；固定骨架、演化局部函数；用多个种群保留不同思路。 |
| AlphaEvolve | [论文 §2](https://arxiv.org/html/2506.13131v1)；[DeepMind 发布说明](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/) | 模型根据程序及评测反馈提出代码修改；支持标记代码块和多层评测；应按预算分配昂贵评测。 |
| ALE-Bench / ALE-Agent | [论文 §4、附录 B](https://arxiv.org/html/2506.09050v1)；[官方代码](https://github.com/SakanaAI/ALE-Bench) | ALE-Agent 是带宽度扩展的 best-first 程序树搜索：结合领域提示、代码反馈和有限修复。不能把 ALE-Bench 的通用评测脚本直接视为完整 ALE-Agent 复现。 |
| OpenEvolve | [项目仓库](https://github.com/algorithmicsuperintelligence/openevolve) | 已有开源 AlphaEvolve 风格工程框架，因此本项目聚焦小而可审计的闭环，不声称首次实现代码进化。 |
| CodeEvolve | [作者论文](https://arxiv.org/abs/2510.14150) | 多岛与代码组合提供另一种可行路线；其报告结果依赖具体任务与预算，不能直接外推至 AHC001。 |
| AHC001 | [AtCoder 原题](https://atcoder.jp/contests/ahc001/tasks/ahc001_a)；[ALE-Bench 数据集](https://huggingface.co/datasets/SakanaAI/ALE-Bench) | 有连续质量分数、严格几何约束、可复现输入和快速评测，适合作为首个闭环任务。 |

本地核对的 ALE-Bench commit：`3da9b12fb5d112dabb3af693d1a42031c95142bc`。
数据包来源：`https://huggingface.co/datasets/SakanaAI/ALE-Bench/resolve/0f42617/ahc001.zip`。
源码许可为 Apache-2.0；数据集标注 CC-BY-ND-4.0。上游保持原样放在忽略目录，不将其重新授权为本项目代码。

## MOSAIC 的设计

MOSAIC = Modular Optimization via Search, Archive and Iterative Code evolution。

演化单元为 `parameters`、`priority`、`search` 三个源码块。搜索块允许增加辅助函数、重写整个 `solve`，所以搜索空间包含算法结构变化，而不局限于超参数。固定的输入输出代码有助于降低接口错误；独立计分器始终在候选程序外部。

每轮从一座岛选择父代，从下一座岛取一个启发程序。25% 的父代选择为档案均匀抽样，其余为三选一竞赛。档案按独立评测得到的空间覆盖率和低满意度广告比例分格，每个格子保留训练评分最高的程序。每隔若干轮迁移精英。这只是小型 MAP-Elites 风格档案，不等于完整复现 AlphaEvolve 的数据库机制。

变异算子包括参数、优先策略、局部搜索、跨程序组合、求解器重写。UCB1 根据成功改进和档案更新反馈分配尝试次数；错误也消耗一次尝试。真实模型返回“可检验修改假设 + 完整替换块”的 JSON，块外源码不会被字符串补丁意外覆盖。语法错误、运行失败和逐例弱点作为下一次有限修复的上下文。

评测顺序：语法检查 → 固定训练子集筛选 → 全训练集 → 选型验证集。筛选时比较父子在**同一子集**上的均分；保留小概率探索，降低误杀新算法的风险。完整训练结果用于档案，验证集用于挑选最终程序。排序分数为 `mean - 0.5 * standard_error`，只是稳定性启发式，不是统计置信下界。

冻结最终程序后才读取留出输入。初始程序与最终程序在相同留出集上作配对比较，留出结果不反馈给模型。最终程序及基线源码以 SHA-256 标识；所有候选、父代、修改假设、请求响应、逐例输出和状态均可追溯。续跑恢复随机状态、岛档案、算子统计，数据哈希和配置不一致会拒绝续跑。

## AHC001 的任务建模与计分边界

目标是把指定点对应的广告放入整数矩形。评分使用题目公式，原始单例分数上限为 1e9；项目内部除以 1e9 后用于搜索。初始算法从 1×1 矩形开始，按随机顺序、固定步长向四边扩张，始终保持包含指定点和无重叠。

离线算子使用人工编写的有限规则：改变扩张顺序、构造尺度、多次重启、矩形平移和边界变动、退火接受。它能验证工程闭环及有限算法族内搜索，但不能替代 LLM 自动算法发现的实验证据。真实模型可自由生成三个块内的 Python 算法。

独立计分器逐对检查所有矩形重叠。上游 Rust `compute_score_details` 对不含指定点的矩形有提前 `continue`，因此某些违规重叠可能漏检。本地实现遵守题面，主动关闭这个漏洞；正常合法输出采用相同计分公式。ALE 适配器会核对官方输出分数与本地严格检查，发生分歧不会接受候选。

## 实验协议与局限

- 本地合成数据遵循题面分布，但使用 Python RNG，**不是** Rust ChaCha20 的逐 seed 复现。官方样例只是附加训练样例。
- `prepare --source ale-public` 使用官方 `session.public_seeds` 和 `case_gen` 生成真正公共样例，再作内部划分；内部 holdout 仍然是官方公共集的一部分。
- `ale-final --private` 才调用一次终局 `private_eval`，产生正式 private score/rank/performance。搜索过程不调用它。
- 调研过程中一次元数据文件预览意外包含 private seed 字段；这些内容没有进入配置、生成器、提示词或搜索选择。本次本地实验不主张符合完整的官方盲测规程。运行器只通过公共 API 获取搜索数据。
- 不使用比赛题解或高分提交作为 seed。本次性能不可与不同机器、语言、时间预算及模型预算的论文数值直接比较。
- 本地进程后端只用于可信规则和测试，不是安全沙箱；真实模型默认 Docker 或 ALE。Docker 禁网、只读文件系统、非 root、限制内存/进程数，只挂载候选源码。
- 本版模型请求串行，测试样例并行。未实现异步多模型群、视觉反馈、模型提示词自演化或多任务泛化；这些是后续扩展，不能算作已实现功能。
- 目前搜索预算是迭代数、请求数与每次输出 token 上限；token 使用量记录来自服务端，不虚构费用。尚无精确美元预算器。

## DeepSeek 接口核实

根据 [官方接口](https://api-docs.deepseek.com/) 和 [模型列表说明](https://api-docs.deepseek.com/quick_start/pricing/)，base URL 为 `https://api.deepseek.com`，配置模型为 `deepseek-flash`。运行前以 `/models` 验证账号实际可用名称；文档与账号返回不一致时，以实时检查为准并显式修改配置。密钥通过环境变量或不回显输入传入，不保存到实验文件。

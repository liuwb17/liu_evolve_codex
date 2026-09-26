# liu_evolve_codex — v3 / v5 自动算法设计框架

面向后续 code agent 的实现说明与实验交接。文档整理日期：2026-09-26。

本仓库保存两个可研究、可修改的自动算法设计（Automatic Algorithm Design, AAD）框架：
**v3：LLM 冷启动 + 多样性档案 + 全程序演化 + 本地数值优化**；
**v5：LLM 冷启动 + 强父代优先 + 有界探索调度 + 协调代码编辑 + 重复评测确认**。

当前任务是 ALE-Bench 的 AHC001（AtCoder Ad）。本仓库是研究原型，不是通用成熟服务，也不声称完整复现 AlphaEvolve / ALE-Agent。
**框架为 Python；这两个历史版本中，模型生成的求解器为 C++17，不是 Python。**
用户已经提出希望未来改为“Python 框架 + Python 求解器”，但这一迁移尚未实施。

## 目录

1. [交接范围、实验状态与阅读路线](#1-交接范围实验状态与阅读路线)
2. [问题定义、术语与数据边界](#2-问题定义术语与数据边界)
3. [系统架构与代码地图](#3-系统架构与代码地图)
4. [v3 的设计与实际执行](#4-v3-的设计与实际执行)
5. [v3 第二层：本地参数进化](#5-v3-第二层本地参数进化)
6. [为什么走到 v5](#6-为什么走到-v5)
7. [v5 的完整设计与实现细节](#7-v5-的完整设计与实现细节)
8. [评测、计时、缓存与安全](#8-评测计时缓存与安全)
9. [模型接口、预算与中断恢复](#9-模型接口预算与中断恢复)
10. [安装、测试和运行](#10-安装测试和运行)
11. [产物结构与来源审计](#11-产物结构与来源审计)
12. [历史结果及解释边界](#12-历史结果及解释边界)
13. [已知问题与改进路线](#13-已知问题与改进路线)
14. [给接手 code agent 的工作要求](#14-给接手-code-agent-的工作要求)

## 1. 交接范围、实验状态与阅读路线

### 1.1 本次 Git 交付包含什么

- v3 / v5 的控制器、模型适配器、评测器、审计器、测试与设计文档。
- v5 实际复用的 v4 公共方法，以及 v1/v2 的公共依赖和历史源文件。它们留在仓库不等于被提供给 v3/v5 的模型。
- 历史实验专用启动脚本，供理解流程；部分依赖未上传的本地实验目录，不能直接当成通用 CLI。
- 不包含密钥、环境文件、原始模型请求/响应、模型推理文本、候选集合、编译产物、下载的数据包、公开/私有实例文件或人类参考源码。
- runs/、data/、upstream/、experiments/、dist/、build/ 均不上传。新机器需要重新准备数据；不会继承本机的断点和历史结果证据。

旧 README 保存在 [历史快照](docs/README_LEGACY.md)，仅用于追溯。旧文档中的“进行中”“尚未测试”、硬编码日期与早期默认配置不是当前状态；涉及实际运行，以该次 protocol、ledger、state、终测报告为准。

### 1.2 停止状态

本次交接没有授权重新启动任何模型实验。

- v5 GLM-5.2：2026-09-26 14:01:46（UTC+8）已按用户要求停止；93/128 个请求名额已占用，最后一个请求中断，用量未知。最佳重复训练均分 988.007133M；尚未做 private 1000 例测试。
- v5 Pro 和第二次 v5 Flash 实验保持暂停。
- 单元测试、文档整理和 Git 推送不是恢复付费实验的授权。

不要在克隆后自动运行带日期的启动脚本、删除 STOP_AFTER_CURRENT，或根据历史预算自行恢复。

### 1.3 推荐阅读路线

1. 先阅读本 README 的数据边界和两版设计。
2. v3：scripts/v3_search.py → aad/v3_provider.py → scripts/v3_parameter_search.py → scripts/v3_audit.py。
3. v5：scripts/v5_search.py → aad/v5_engine.py → aad/v5_policy.py → aad/v5_provider.py。
4. 阅读 aad/v4_engine.py 中被 v5 继承的方法：save / source / mean / invoke / measure / repeat。
5. 阅读 aad/v4_protocol.py 中 vectors / paired_decision；不要误认为 v5 使用了 v4 的 UCB 调度。
6. 评测：aad/v3_evaluator.py → aad/cpp.py → aad/assets/v3_timer.py → aad/problem.py。
7. 测试：tests/test_v3.py、test_numeric_workflow.py、test_v5.py、test_v5_glm.py。
8. 最后看历史 orchestrator 和结果解释，不要从旧 README 的 v1 CLI 推断 v3/v5 行为。

## 2. 问题定义、术语与数据边界

### 2.1 AHC001 优化什么

输入 n 个点及目标面积，输出位于 10000 × 10000 平面中的 n 个整数坐标矩形。
矩形不能有正面积交叠。包含对应指定点的矩形，以实际面积 s 与目标面积 r 计算满足度：

~~~text
satisfaction = 1 - (1 - min(s, r) / max(s, r))²
score = round(1e9 × mean(satisfaction))
~~~

未包含指定点的矩形贡献零满足度；坐标或交叠非法会导致输出不合法。以独立 checker 和原始 Rust 工具的实际判断为准。
“提高分数”不是让矩形越大越好，而是尽可能满足所有目标面积，同时保持几何可行。

框架不预先规定必须用退火、贪心、分割或某种邻域；初代构造和搜索机制由模型产生。
题目文本位于 aad/v3_provider.py 的 PROBLEM，既规定优化目标，也规定 argv[1] 时间预算和 I/O 合约。

### 2.2 这里的“训练”不是训练模型权重

模型权重固定；变化的是源代码、参数和候选档案。
“父代”是已有程序，“变异”是模型修改程序，“交叉”是让模型尝试转移另一程序的机制，而不是随机拼接文本。

区分这些计数：

| 名称 | 含义 |
|---|---|
| API 请求 | 一次实际生成尝试；网络失败、无效响应也占名额 |
| 提案 / event | 一次控制器安排的设计或修改任务；可能失败或产生重复代码 |
| 候选 | 按代码哈希去重后的程序；不保证可编译或合法 |
| 有效完整候选 | 完成 40 例训练评测且全部合法 |
| incumbent | v5 已通过重复确认的当前最佳程序 |
| archive / pool | 用于选父代的有效候选集合；成员不一定都完成三次重复 |
| 参数 variant | v3 本地选择的一组数值，不是一次 API 请求 |
| 冻结 | 关闭开发并固定最终程序，之后才能终测 |

### 2.3 分数单位

程序逐例 score 使用原始 0～1e9 分数；内部 mean 通常归一化到 0～1。
报告中的 M 表示百万原始分：

~~~text
0.988007133 normalized = 988,007,133 raw = 988.007133M
0.00005 normalized = 50,000 raw = 0.05M
0.0002 normalized = 200,000 raw = 0.2M
~~~

不要把 0.05M 写成 0.05 的归一化提升。

### 2.4 公开训练 / 验证 / 私有测试

- 50 个 public 输入由官方 Rust gen 使用官方 public seeds 生成。
- public_cases() 将 manifest 中所有 public 文件合并、按文件名排序。
- 下标 i % 5 != 4 的 40 例为训练；i % 5 == 4 的 10 例为最终验证。
- 数据准备工具原有 manifest 的 train/validation/holdout 名称是旧版划分，**v3/v5 不直接沿用这些分组**，而是重新执行上述 40/10 划分。
- 验证集可以最终选型，所以不能叫完全未参与选择的测试集。
- 官方 private 1000 例仅在冻结后评测；开发阶段不得将其输入、输出、逐例分数或诊断加入 prompt。
- 原始系统种子必须与 ALE-Bench private seeds 的数量、内容、顺序完全一致，再由原始 Rust 工具生成输入。Python RNG 合成数据不是它的替代品。
- 整个项目已经多次观察过 private 汇总结果，因此不能再声称项目级完全盲测；“本轮 prompt 没有 private 数据”和“研究从未见过测试信息”是两回事。

所谓冷启动，只保证模型调用不接收旧程序或人工算法骨架；无法排除预训练知识。
控制器、诊断、评测和选择规则是人工工程贡献，不能归因于 LLM 自发发明。

## 3. 系统架构与代码地图

~~~text
任务题面 + 预算
      ↓
Python 控制器 ── 选父代 / 算子 / donor / 训练反馈
      ↓
模型适配器 ──── 先记请求账本，再发送请求，保存原始响应
      ↓
源码解析与组装 ─ 完整程序 / 精确编辑 / v3 参数块
      ↓
编译 + 隔离执行 + 独立合法性与评分
      ↓
更新候选档案 / 确认最佳 / 保存状态 ──→ 下一轮
      ↓ 开发结束
公开验证选型 → 冻结 → 来源审计 → 官方 private 终测 → Rust 复核
~~~

| 层 | v3 | v5 / 公共模块 |
|---|---|---|
| 通用搜索入口 | scripts/v3_search.py | scripts/v5_search.py |
| 策略 / 状态机 | v3_search.py 内函数与主循环 | aad/v5_engine.py、aad/v5_policy.py |
| 模型传输 | aad/v3_provider.py | aad/v5_provider.py；GLM 为 aad/v5_glm_provider.py |
| 复用基础 | aad/provider.py 等 | aad/v4_engine.py、aad/v4_provider.py、aad/v4_protocol.py |
| 本地参数搜索 | scripts/v3_parameter_search.py、aad/parameters.py | 无独立数值搜索阶段 |
| 评测 | aad/v3_evaluator.py、aad/cpp.py、aad/assets/v3_timer.py | 两版共用 |
| 几何诊断 | aad/v3_diagnostics.py | 两版共用 |
| 来源审计 | scripts/v3_audit.py | scripts/v5_audit.py |
| 只读状态 | scripts/v3_status.py | scripts/v5_status.py |
| 冻结后终测 | scripts/v3_system_test.py | scripts/v5_system_test.py，委托 v3 终测 |
| 官方工具 | scripts/official_tools.py、system_test.py、rust_score_batch.py | 两版共用 |

scripts/v3_rerun_256.py、v3_finish_256.py、v5_run_experiment.py、v5_pro_256.py、v5_flash_repeat2.py、v5_glm52_128.py 是历史实验编排，不是完全可移植的一键入口。
GLM、Pro、Flash 重复实验的脚本还可能引用未上传的参考 protocol、历史完成标记和基线目录。

## 4. v3 的设计与实际执行

### 4.1 核心假设

单个初代可能把整个谱系锁在较差表示中。因此应同时保留多种路线，让模型既能深入改进，也能重启和借鉴其他候选；算法结构确定后，再用本地数值优化精调参数。

v3 不是标准的按代淘汰一整群个体的遗传算法，而是一个顺序、异步可恢复的程序提案循环。

### 4.2 初始化与独立重启

初代默认安排 8 次 independent_initial_design 尝试，不是保证得到 8 个合法程序。
初代任务只给题目、目标和预算，不提供前面初代的源码或分数。

编译 / 运行失败可安排一次针对性 repair。repair 失败不无限递归修复。
后续 --restart-patience 6 表示：距最近一次训练目标提升或独立采样已经完成至少 6 个无进展事件，就安排 problem-only 独立重启。
重启不删除已有档案。novel_design 与 independent_restart 不同：前者可以收到父代，不能把它当成完全独立初代。

### 4.3 候选目标与多样性档案

训练 objective：

~~~text
objective = 0.9 × training_mean + 0.1 × mean(lowest_quartile_case_scores)
~~~

仅合法完整评测候选参与。维护档案时保留：

1. objective 前三名；
2. 按点数排序后的四个规模分层，各自表现最好的专家；
3. 逐例分数非支配前沿中，不同模型自述 family 的代表，总体最多约 10 个。

“非支配”意为不存在另一个程序在所有训练实例都不差、且至少一例更好。
family 是模型描述，不是结构自动证明；两个名称不同的程序仍可能非常相似。

父代选择为 55% 选择 objective 最优，45% 从档案随机取样。
所以 v3 的最强父代不一定是纯训练均分最高者。

### 4.4 操作类型

基础循环包含 targeted_improvement、structural_redesign、bottleneck_optimization、complementary_crossover、robustness、novel_design。
repair 与停滞重启可以插入；按事件索引调度，不是严格独立的“遗传代”。

- targeted_improvement：针对一个测得的弱点进行改进。
- structural_redesign：改变表示、构造或搜索组织。
- bottleneck_optimization：提高单位时间内的有效搜索。
- complementary_crossover：引入另一个候选的有用机制，禁止硬编码实例路由。
- robustness：改善低分实例，尽量不牺牲均分。
- novel_design：挑战当前设计，通常仍有父代上下文。

响应通常是包含 hypothesis、family、code 的 JSON；code 为完整 C++17 程序。

### 4.5 donor 如何选择

逐例互补 donor 的目标为：

~~~text
sum_i max(0, donor_score_i - parent_score_i)
~~~

它衡量“在哪些实例上有可以借鉴的优势”，而不是只看 donor 总均分。
后期 v3 交替使用机制 donor：最近的合法重设计 / 新设计 / 独立重启，均分不能落后父代超过 40M。
机制 donor 不一定在主档案里。模型收到选择原因和较低分数，控制器不指定必须转移哪种几何动作。

### 4.6 评测与反馈

早期版本：
6 例短预算合法性 → 12 例短预算 → 12 例完整预算 → 40 例完整预算。
这种筛选可能误杀慢启动算法，且模型生成比评测更昂贵。

后期及 Flash 重跑使用 --full-evaluation：
保留短测合法性检查，所有通过短测的候选都评测 40 例完整预算，不按短测分数淘汰。
代码仍保留两条分支；阅读或新跑时务必明确选了哪条。

反馈包含完整/短测摘要、耗时、训练点数，以及两个低分训练实例的输入、程序输出和几何诊断。
几何诊断测量面积缺口、固定其余边时的可扩展距离、阻挡矩形等；它不生成参考解。
RE 可以额外运行 SanitizerEvaluator 定位内存错误；诊断构建不能参与性能排名。

### 4.7 最终选型

关闭开发后，将 objective 前三和训练均分领先者（必要时补第四名）固定为候选，在 10 例验证集比较。
按验证均分选优，objective 只作同分次序。
v3 没有 v5 那样的三重复训练确认 / 保守验证 gate。
写出 best.cpp、champion 和 frozen 后，不允许继续演化。

v3 首版是运行过程中多次记录策略修订的开发轨迹，不是一开始就完全固定的协议。
详见 [协议及变更历史](docs/V3_PROTOCOL.md)、[Flash 重跑](docs/V3_RERUN_256.md)。

## 5. v3 第二层：本地参数进化

### 5.1 为什么有第二层

让 LLM 为每个温度、概率或阈值都花一次 API 调用不经济。
因此让模型决定“哪些旋钮值得调、范围是什么”，由通用本地优化器决定具体数值组合。

这个阶段是结构演化后的附加层，不与每次代码变异完全交错运行。

### 5.2 参数化合约

模型暴露重要参数，prompt 要求最多 6 项，解析器实际允许 1～8 项。
每项包含名称、整数/浮点、线性/对数尺度、上下界、默认值。
argv[1] 仍是搜索秒数，后续 argv 为参数。

禁止把总时限、随机种子、输入数据和题目常量伪装成可调参数。
接口须真的影响算法；解析了参数但不用它，不算成功参数化。

先做默认程序的短测和完整训练评测；默认均分比原程序下降超过 2M 要求修复。
另检查显式传默认值与不传参数的差异。这是经验检测，不是严格语义等价证明。
参数化即使“看似只提取常量”，也可能改变随机调用路径、舍入或控制流。

### 5.3 搜索与写回

- 最多 64 个不重复组合，包括默认组合；不是额外 64 次 API。
- DE/rand/1/bin；差分系数 0.7、交叉率 0.85、种群 8～16，固定本地搜索随机种子。
- 每组先跑既定 12 个训练实例，每例完整 4.8 秒。
- 前四名及默认组合再跑全部 40 例与短测合法性复核。
- 非默认组合优于原父代超过 0.05M 才尝试写回。
- 再请求模型返回受限 constexpr 参数块，区域外代码逐字保留。
- 重新评测最终无额外参数的独立程序，不能把 variant 的试验分数直接当成最终源码分数。

参数数值由控制器选择，这部分贡献不能声称全部来自模型。
源代码审计区分 full-program 与 parameter-block 组装来源。
历史上遇到过未生效参数、重复源码对应 schema 更新丢失、参数化性能下降；相关失败和修复在 [数值层说明](docs/V3_NUMERIC_LAYER.md)。

## 6. 为什么走到 v5

v4 曾尝试“两次调用：先设计、再实现”、单位置编辑、多岛轮流投资和成本感知 UCB，但一次 Flash 实验明显退步。
观察包括：

- 程序平均约 0.166 秒结束，远未用完 4.8 秒；局部搜索结束后缺乏有效继续探索机制。
- 133 个事件中有 70 个交叉，交叉预算过多。
- 某高分候选第二次评测出现矩形交叠；排除正确，但复测失败没有进入修复反馈。
- 两次 API 才形成一个代码提案；单处编辑不便于同步修改多个相关函数。

这些是观察。不能仅凭一次实验证明“两步调用”“UCB”或“多岛”本身无效。
v5 是针对这些失败模式的工程修订，而不是已经完成严格消融验证的理论最优方案。
详见 [v5 设计](docs/V5_DESIGN.md)、[v4 历史设计](docs/V4_DESIGN.md)。

## 7. v5 的完整设计与实现细节

### 7.1 单次思考生成一个代码提案

每次提案用一个 thinking-enabled 请求；不另外付一次 API 写设计。
完整程序以简短假设注释开头，必须以 // AAD_V5_COMPLETE 结束。
局部编辑返回 JSON；其他操作返回完整 C++，避免把大程序全部嵌套在 JSON 字符串中。

一次请求不保证得到有效候选：截断、空响应、格式错误、重复源码、编译错误都会损耗预算。
provider 的 status=ok 只表示传输层响应通过，不表示源码解析或评测成功。

### 7.2 初代与谱系

目标是 4 个有效独立谱系，最多 20 次初代尝试。
判据来自 valid_for_archive；独立候选还需通过后续重复评测。
repair 优先于新初代；20 次是独立初代尝试上限，不是把所有 repair 也算入的总 API 上限。
若没有一个可用初代且达到尝试上限，失败退出；若已有可用初代，允许带着不足四条谱系进入后续搜索。

每个独立根的 lineage 是其源码哈希；子代继承主父代 lineage。
即使交叉使用另一个 donor，lineage 也不表示完整双亲祖先树；donor 字段另行保存。

### 7.3 档案和强父代

按 repeated_mean 排序，最多 8 个，每个 lineage 最多 2 个。
有已取得的合法 repeats 时取它们的均值，否则取 full.mean。
注意：首次完整评测后就会有长度为 1 的 repeats；档案可以混合一次评测候选和三次评测候选，不能把 archive 第一名自动称为“已确认冠军”。

常规 slot 中四分之三选择 archive[0]，第四个轮转其他档案成员。
这是确定性 slot 规则，不是独立 75% 随机抽样；独立设计和修复不完全受这个比例约束。
incumbent 单独存放，表示已经通过确认 gate 的最优者。

### 7.4 有界算子调度

基础八槽循环：

~~~text
local → redesign → runtime → local → crossover → redesign → local → independent
~~~

choose_operator 再做修正：

- 最近 7 个事件已有交叉，本次交叉槽改为 redesign，形成最近 8 个事件最多一次交叉。
- 对非 independent / redesign 算子，从最近 16 个事件中取该算子最近 3 次；若父代收益都不超过 0.05M，则改为 redesign。
- independent 只接收题目，不接收父代代码与旧反馈。
- crossover 必须有不同 lineage 的 donor，而且至少一个训练实例优于父代；否则改为 redesign。

parent_gain 是相对所选父代的收益，不是相对全局 incumbent；gain 才记录当前最佳的改进。
它们都是训练观察值，不是因果归因或显著性证明。

### 7.5 运行预算干预

反馈比较同一批 smoke 实例在 0.6 秒与 4.8 秒的成绩，并报告完整评测平均进程耗时。
若平均不足 2.4 秒且归一化均分小于 0.999，则标记 underutilized。
对同一父代，最近没有 runtime 任务或距上一次至少 8 个事件时，可覆盖常规 slot，安排 runtime 任务。

要求模型设计有收益的继续搜索，不允许 sleep、空转、无效重复、单纯伪造耗时。
程序不必为了指标强行耗尽预算。v3 已有耗时诊断；v5 新增的是更明确的调度干预。

### 7.6 局部协调编辑合约

最多 6 处编辑，不是 6 个算法参数。
payload 必须包含 hypothesis、parent_sha256、edits。
每个 old 必须在原始父代中恰好出现一次；所有 old 区间必须互不重叠。
控制器先定位原始源码，再从后往前替换，防止前一编辑改变后一编辑的位置。
不做模糊匹配，不人工“猜测模型本来想改哪里”。最终程序仍须满足完整源码标记合约。

适合一次同时调整声明、辅助函数及其调用点。完整 redesign / runtime / crossover / repair 没有局部六处限制。

### 7.7 一次提案怎样成为新最佳

~~~text
保存 pending 和 call ID
→ 发送或恢复已保存响应
→ 解析 / 应用编辑 / 源码哈希去重
→ 6 例 × 0.6 秒合法性
→ 40 例 × 4.8 秒首次完整训练
→ 值得复测？完成总计 3 次完整训练
→ 确认晋级或保留在档案
→ 写 event、清空 pending
~~~

复测条件：首次 full.mean >= incumbent_mean - 0.0005（允许落后 0.5M），或者无父代的独立候选。
其余合法候选可入档案，但不马上花额外两次评测。

新 incumbent 要求：

1. 三次评测均合法；
2. 对应重复编号的均分增益平均 > 0.00005，即 0.05M；
3. 三个增益至少两个为正。

训练阶段不做跨实例 bootstrap 显著性 gate，目的是不把适应性搜索卡得太保守。
0.05M / 2-of-3 是工程策略，不是统计显著性结论。
重复评测不新增训练实例；不保证改变算法随机种子，主要反映重新执行与墙钟时限带来的波动。

### 7.8 失败修复

编译、运行、合法性失败以及确认重复中才出现的失败，都可回流一次 repair。
反馈优先选失败重复中的实际输入输出；合法低分则补充几何诊断。
repair 再失败不立即无限递归。

格式解析失败通常被记录为 failed，但当前并没有完善的分层格式修复策略。
GLM 实验中缺结束标记和非法 JSON 消耗了明显预算，这是值得改进的缺陷，而不是“模型一定不会设计算法”的证据。

### 7.9 最终验证

finalize 首先设置 development_closed，固定最多三个通过训练重复合法性检查的候选：incumbent 加档案中的两个。
然后设置 validation_started，在 10 例验证集各跑三次。

比较过程：

- 每个实例先对重复求均值，再计算候选相对原 incumbent 的逐例差值。
- 固定随机种子的 2000 次 case-cluster bootstrap。
- 平均提升 > 0.2M 且 bootstrap 下界 > 0 才替换。
- alpha 对本次挑战者数量做局部调整；不能因此宣称控制了整个适应性研究的多重选择偏差。
- 其他候选都对原 incumbent 比较，不是不断改对照的淘汰赛。
- 差异不明确时保留 incumbent；incumbent 验证非法则停止，不偷偷替换后继续 private。

最后保存 frozen、champion、best.cpp、report.json。
这里的 report.json 标注 private_evaluated=false；终测报告在另一个输出目录，不能混读。

## 8. 评测、计时、缓存与安全

### 8.1 执行合约

CleanEvaluator 复用 CppEvaluator 编译器，正常评测使用优化构建。
候选运行容器为无网络、只读、非 root、去 capabilities、no-new-privileges、最多 32 进程、1 CPU、512 MiB。
候选只挂载自己的构建目录和无算法的 timer，不挂载仓库、数据全集、旧解法或密钥。

timer 给子进程仅设置最小 PATH；stdin 传当前实例，stdout 收矩形。
每个输出流最多保留 64 KiB，越界记 OLE；RE / TLE / 非法结果计零，不从平均分分母中删除。

### 8.2 时间限制的两层含义

- 0.6 秒短测：候选硬限 min(5, 0.6+0.15)=0.75 秒。
- 4.8 秒完整测：候选硬限 4.95 秒，包含启动和输入输出。
- Docker 外部基础设施调用另有 30 秒上限，不计为算法搜索时间。
- 最多 6 个实例并发，每个限制 1 CPU；这不是整个实验只占一个 CPU。

v3 protocol.json 中历史 hard_seconds=5.0 字段不够精确；实际 timer 对完整测执行 4.95 秒。
应核对 timer / receipt，不能只引用旧元数据字段。
镜像固定 ID 不代表硬件等同官方环境；主机负载、CPU、Docker 配置仍会改变分数。

### 8.3 缓存与重复

评测缓存键包含代码、实例指纹、镜像、搜索秒数、timer 版本；参数模式额外加入参数向量。
v5 各重复使用不同 measurements 子目录，避免三次实际都命中同一结果。
修改 evaluator 行为时必须增加版本隔离或新目录，不能混用旧缓存。

六个 worker 并发不代表可以同时运行多套六 worker 的计时实验。
历史 wrappers 通过 pro64_experiments.evaluation_slot 做共享锁，但通用 v5_search.py 本身不保证跨实验串行。
API 等待可以并行；正式 CPU 评测需要统一资源调度。

### 8.4 独立计分与官方 Rust

开发时用 aad/problem.py 独立合法性与评分。
最终逐例输出再交给原始 Rust vis，要求分数一致，并报告 min/max、AC 数、耗时、差异数量。
官方数据生成不等于官方 Session 排名。此项目历史成绩是本机容器、官方实例及工具上的实测。

Docker 是防御措施，不是对恶意模型代码的形式化安全保证。不要改为直接在宿主机运行不可信候选。
审计脚本使用了一些 assert，运行审计时不要使用 python -O；未来应改为显式异常并补测试。

## 9. 模型接口、预算与中断恢复

### 9.1 当前适配器并非完全可配置

| 路径 | 当前行为 |
|---|---|
| v3_search + ProgramProposer | 主入口配置 DeepSeek URL；Chat Completions JSON 完整程序 |
| v5_search + V5Provider | endpoint 写在适配器内，默认 DeepSeek；可选 model，不等于可选任意供应商 URL |
| GLM5Provider | BigModel glm-5.2 专用 SSE 流式适配器，使用 ZHIPU_API_KEY |
| 旧 aad CLI/configs | 主要属于 v1 路径，不会自动重配置 v3/v5 |

不要仅修改 configs/deepseek.json 就认为 v5 已切换供应商。
通用化需要单独设计 endpoint、model、reasoning、响应格式和 transport 配置，并记录协议变化。

v5 DeepSeek 请求启用 thinking、reasoning_effort=low、max_tokens=65536；非流式、600 秒网络超时。
GLM 保持生成 prompt 一致，但 transport 为 SSE；相同 effort 字符串不保证供应商内部推理算力相同。
模型名称和价格会变化；本 README 记录历史请求配置，不保证未来服务端路由。

### 9.2 账本

在发出 HTTP 前保存 request.json、预留 call ID 并写入 ledger。
所有失败请求计入 calls；网络失败或强制中断的用量不明，不能按免费处理。
provider.requests 是账本长度，不是成功次数；candidate 数和 event 数都可能不同。

max_tokens 是单次输出上限，不是实际消耗。
token_budget=0 表示不额外设总 token 上限；正数预算基于已报告 usage 或保守预留，不是账单精确封顶保证。
等 API 次数不等 token、费用、时间或有效提案数。

不在代码、配置、命令行、日志或 Git 中写 API key。
支持环境变量与隐藏输入；Windows environment() 还可读取明确命名的当前用户环境变量。

### 9.3 GLM 流式记录

aad/glm_stream.py 分开累计 reasoning 与 content，要求完整流结束和 finish_reason。
保存 stream.jsonl、progress.json、最终标准 response.json，部分流不能当完整程序使用。
空闲 timeout 180 秒、总时长限制 3600 秒；总时长检查发生在读流之间，不是独立于网络阻塞的精密硬计时器。
HTTP/网络错误 fail closed 停止 GLM 实验，不暗中重试。格式无效可记录后继续主循环。

### 9.4 恢复语义

v5 pending 在发请求前持久化 implementation_call。
若恢复时该调用已有成功 response，重用响应，避免重复付费。
若账本显示旧请求未成功，则记录失败，不把相同 ID 当新请求重发。
重新构造 provider 时 started 会被标为 interrupted_usage_unknown。

STOP_AFTER_CURRENT 是安全边界停止，不是远程 API 取消；当前调用和评测可能继续到边界。
立即杀进程会留下 pending 和过时 orchestration，必须保存停止回执；关闭本地连接不保证服务端不再计费。

v5 signature 绑定配置、数据、镜像和多份实现哈希。更改预算、模型或代码后直接 --resume 可能拒绝，这是预期保护。
不要为了继续跑删除 signature 或篡改旧结果；需新实验或明确的、留痕的迁移。
同一 run 只允许一个写入者；锁主要由外部 wrappers 提供，并非每个裸 CLI 都自行保证。

## 10. 安装、测试和运行

### 10.1 最小环境

- Python >= 3.11，基础包仅标准库；可在仓库根目录直接运行脚本。
- Docker Linux containers；C++ 编译与候选执行在容器中。
- 官方原始数据包、Rust 工具和 public 输入另行准备。
- 数据和镜像未包含在 Git 中；Dockerfile 使用可变 apt 仓库，重建镜像不保证复现历史二进制。

~~~bash
python -m unittest discover -s tests -q
# 可选：安装本项目 CLI
python -m pip install -e .
# 构建评测镜像（涉及网络）
docker build -f docker/Dockerfile.cpp -t mosaic-cpp:1 docker
~~~

本次交付已在原工作目录通过 90 项测试；测试包含 mock 和人工离线流程，不产生真实模型成绩，也没有付费 API 调用。
公开数据加载、排序、40/10 分割与指纹篡改检测使用临时合成夹具做单元验证，不将夹具称为官方数据；另保留一项真实公开数据集成检查，在干净 clone 未安装数据时明确 skip。
测试通过不证明新机器已具有 Docker / 官方工具 / 模型访问能力。

### 10.2 准备官方 public 数据

原始数据来源与上游 revision 见 [ALE-Bench 接入说明](docs/ALE_BENCH.md)。
历史固定数据包地址：
https://huggingface.co/datasets/SakanaAI/ALE-Bench/resolve/0f42617/ahc001.zip

下载并解压，使 data/reference/ahc001/data.json 和 tools/ 存在。注意 zip 的许可与上游使用条件。
新目录执行：

~~~bash
docker pull rust:1.85-slim
python scripts/official_tools.py build
python scripts/official_tools.py prepare
~~~

prepare 遇到已有 manifest 会拒绝覆盖；不要为了方便删除旧实验数据。
v3/v5 会校验所有 50 个 public 输入指纹，然后建立自己的 40/10 split。
official_tools.py verify 的默认路径是旧 Python v1 对照，不是通用 v5 验证命令。

### 10.3 新 v3 实验

下面是一个 64 次累计预算的示例，不是复刻所有历史中途配置变更。命令会产生 API 费用，须先获得授权。

~~~bash
python scripts/v3_search.py --run-dir runs/v3_new --calls 60 --initial 8 --model deepseek-flash --thinking enabled --effort low --exploration-effort high --max-tokens 65536 --full-evaluation --restart-patience 6 --ask-key
~~~

结构阶段最多 60 次，给参数层最多预留 4 次；两阶段共享 ledger。
如果选择做数值层：

~~~bash
python scripts/v3_parameter_search.py --run-dir runs/v3_new --calls 64 --variants 64 --prepare-only --ask-key
# 检查参数接口确实生效，且不含时限、种子或输入常量；通过后：
python scripts/v3_parameter_search.py --run-dir runs/v3_new --calls 64 --variants 64 --ask-key
~~~

具体失败可能耗尽预留次数，不保证完成安装。
最终 v3_search.py --finalize 仍可能先运行剩余生成预算！
如只想立即选型，应把 --calls 设置为当前 ledger 已使用次数，并保留其他原配置；不要误把 --finalize 理解成天然“不再调用模型”。
即使只选型，v3 仍可能做只读模型连接检查和要求凭据。详细命令需依据实际 state/config，不要照搬旧目录。

### 10.4 新 v5 实验

~~~bash
python scripts/v5_search.py --run-dir runs/v5_new --calls 128 --token-budget 0 --model deepseek-flash --ask-key
~~~

未冻结时，用完全相同的协议参数恢复：

~~~bash
python scripts/v5_search.py --run-dir runs/v5_new --calls 128 --token-budget 0 --model deepseek-flash --resume --ask-key
~~~

确认结束开发且没有未完成 pending 后，显式选型：

~~~bash
python scripts/v5_search.py --run-dir runs/v5_new --calls 128 --token-budget 0 --model deepseek-flash --resume --finalize
python scripts/v5_audit.py --run-dir runs/v5_new
python scripts/v5_status.py --run-dir runs/v5_new
~~~

v5 的 finalize 分支不调用生成 API，但仍执行本地训练补充确认与验证评测。
模型、预算、镜像和实现哈希必须与原协议一致；未处理 pending 会拒绝 finalize。
以上不会启动 private 终测。GLM 专用历史 wrapper 不是新机器直接可用的同等 CLI，需先去除参考历史目录耦合并明确新实验协议。

### 10.5 private 1000 例与人类基线：当前存在移植前置条件

官方系统种子来自 https://img.atcoder.jp/ahc001/seeds.zip 。
历史 seeds.txt 的 MD5 为 8fc1ce3f4beabac6abc1bdb4206d7f7e，必须与 ALE private 列表逐项同序一致。
种子和输入不要提前加载到训练控制器。

**目前不能声称“干净 clone 后直接运行 v5_system_test 就可以终测”。**
scripts/v3_system_test.py 会无条件先读取 runs/human_kusano_1000/report.json，即使指定 --paired-baseline 也如此。
v5_system_test.py 默认也引用本机历史 kusano 目录；这些文件未上传。

后续 agent 应先将“冻结程序独立终测”和“可选人类基线配对比较”解耦，补新机器测试，并保持冻结、哈希及官方 Rust 校验。
本次交付不偷偷修正这一行为，不把历史结果包装成已经可移植的端到端验收。
在依赖确实已准备的原环境，才使用：

~~~bash
python scripts/v5_system_test.py --run-dir runs/v5_new --output runs/v5_new_private --paired-baseline runs/verified_matched_human_baseline
~~~

它会访问 private 数据，不应当作为日常开发 smoke test。没有基线时不要编造 baseline 文件来绕过校验。

## 11. 产物结构与来源审计

典型 v5 运行目录（实际不包含在此次 Git 交付）：

~~~text
runs/<new_run>/
  protocol.json                 配置、数据指纹、镜像、实现哈希
  state.json                    records / events / pending / incumbent / frozen
  llm/
    ledger.json                 请求编号、状态、用量、耗时
    00000.request.json          无 Authorization 头的模型请求体
    00000.response.json         模型原始响应
    00000.stream.jsonl          仅流式适配器
    00000.progress.json         仅流式适配器
  candidates/<source_sha256>/
    solution.cpp
  measurements/
    smoke/ train-r0/ train-r1/ train-r2/
    validation-r0/ validation-r1/ validation-r2/
  best.cpp                      冻结后写出
  report.json                   训练 / 验证摘要，不是 private 报告
  source_audit.json
  STOP_AFTER_CURRENT            停止标记（若存在）
~~~

v3 使用不同评测目录并有 numeric_search/，不要把两版 state schema 直接互换。
orchestration.json、resource_status.json 和 console.log 通常由外层 wrapper 创建，不是所有裸 CLI 都存在。

关键不变量：

1. candidate ID 对应实际源码 digest；人工改了源码必须成为新候选，不能沿用 ID。
2. 每个 parent / donor 源码必须来自同一实验、更早的模型调用。
3. 独立初代请求没有旧源码来源边。
4. 完整程序能从单条 response 复原；编辑程序能从原父代和对应 response 精确重建。
5. v3 参数块审计另记模型块来源与控制器选择的数值。
6. 数据输入、评测镜像和缓存版本发生变化，不可混用旧分数。
7. 请求失败保留预算占用，不能为了图好看删除失败条目。
8. private 结果不得成为同一轮之后的演化反馈。

v5_audit 验证候选来源、真实 prompt 中的父代和 donor、因果顺序、响应结束状态及精确重组。
它不是内容级反作弊证明：不能排除预训练记忆，也不证明 prompt 里绝无其他泄漏文本。
原始运行证据未上传，因此接收者不能仅靠 README 的历史汇总独立复核所有成绩。

## 12. 历史结果及解释边界

以下为本项目已完成运行的本机官方 private 1000 例均分；M 为百万原始分。
这是历史摘要，不是本次 Git 整理重新跑出的结果。完整证据原存于本机 runs/，不包含在仓库。

| 实验 | private 均分 |
|---|---:|
| v3 初版（主要为 Pro 的独立冷启动谱系） | 987.815584M |
| v3 Flash 重跑 | 986.446533M |
| v4 Flash（设计反例） | 955.582938M |
| v5 Flash 首轮 | 990.870675M |
| 同镜像、同搜索预算 kusano 对照 | 989.489063M |

v3 初版的总 64 请求预算包含先前独立 Flash 6 次和 Pro 58 次，二者不共享源码，不能写成 64 次 Pro。
v3 Flash 曾在用户授权后放宽完成阶段上限；名称中的“256”不能代替实际 ledger 计数。
v5 Flash 首轮为 256 次请求，private 全部 AC、Rust 分数零差异，配对 726 胜 / 274 负。
其均分超过该本机 kusano 对照约 1.381611M；不是证明赢了官方比赛冠军或官方机器排名。

2026-09-26 停止时，GLM v5 的 988.007133M 是 40 例训练、三次重复均值，不是 private 成绩；不能放进上表直接排名。
早期一次较弱初代不能代表该模型全部能力；比较应同时看初代采样池、等调用进度和多次运行。

所有框架效果都混有模型随机性、有效候选数量、token 消耗、时限与实现变化。
目前没有足够逐项消融证明 v5 的每个机制都有效，亦不能泛化为某模型全面优于另一个模型。

## 13. 已知问题与改进路线

以下是读实现后应该优先考虑的方向，不是已经实现的功能。

### 13.1 优先级 P0：移植性与实验正确性

- 将 endpoint/model/provider、run 路径、预算、语言、数据、worker、time limits 做成统一配置；当前 wrappers 含大量历史路径。
- 解耦独立 private 终测与人类 baseline；没有 baseline 时也应能合法产生单程序终测报告。
- 为干净 clone 增加离线端到端测试与可选 Docker 集成测试，避免单元测试通过但数据准备/终测不可运行。
- 统一主进程锁、评测资源锁、停止状态和回执；当前裸 CLI 与 wrapper 的保证不一致。
- 在 v3 中补齐完整响应结束检查。v3 provider 解析 JSON，但没有 v5 同样显式的 finish_reason=stop gate。
- v5 配置字段并非全部驱动实现：initial_valid_roots、max_initial_attempts、training_mean_margin 等部分实际阈值仍写死在 engine；不要只改 config 就声称改变了策略。
- 审计 assert 改显式异常；进一步保证不存在被优化标志绕过的检查。
- 更强地验证完整输入/输出、时间预算和评测器版本，防止错误缓存被当改进。

### 13.2 优先级 P1：生成效率与失败利用

- 分开统计传输失败、截断、格式错误、编译错误、运行非法、合法但无收益，避免只用 API 成功率解释模型强弱。
- 分层修复：格式层与算法层分开；若增加解析容错，必须定义精确转换规则并可审计，不能人工补完算法。
- 逐步将 provider 抽象成稳定接口，保留每次请求的有效模型、使用量和中断状态。
- 用流式传输降低“很长时间没有最终响应”的脆弱性，但不要把流式进度当完成。
- 增加预算收益指标：每百万 token、每单位费用、每分钟、每个合法提案的提高，而不是只看 calls。

### 13.3 优先级 P1：搜索策略

- 对 v5 的混合一次/三次评测 archive 加不确定性建模；当前强父代可能只是一次幸运高分。
- 对重复失败的同类设计建立机制级失败记忆；当前只给最近八个事件摘要，长程失败可能被遗忘。
- 代码哈希只能去掉完全重复程序，不能识别语义近似或改名复制。
- lineage 多样性不等于算法多样性；研究行为特征、结构表示或性能向量聚类。
- 对固定八槽与收益替换规则做消融；固定配额能限制垄断，但不一定最优。
- 研究结构搜索与数值搜索交错，而不是机械恢复 v3 的尾部参数层。
- donor 的逐例互补优势可能来自噪声；应验证迁移收益，而不是把 donor 标签当机制解释。
- 几何反馈目前偏向两个最差实例，可能过度集中；可以设计覆盖不同失败机制的诊断抽样。

### 13.4 优先级 P1：公平评测

- 同一模型、固定数据/环境，多次独立冷启动再比较框架。
- 除等 API 次数外，报告等 token / 成本 / 墙钟预算曲线。
- CPU contention、评测顺序、热启动等可能影响墙钟截止算法；考虑交错重测和随机顺序。
- 3 次重复不是 120 个独立训练样本；case bootstrap 与适应性选择的偏差要明确。
- official private 已多轮看过；新的研究结论应另设未接触的泛化集，同时保留官方基准的历史一致性。
- 单独消融：强父代比例、协调编辑、runtime 干预、三重复 gate、交叉上限、独立重启。

### 13.5 后续“Python 框架 + Python 算法”迁移

不能只把 solution.cpp 改名 solution.py：

1. 修改 PROBLEM 和生成 / 编辑 / 审计合约，不再要求 C++ main 或 C++ 完成注释。
2. 新建语言适配层，选择 CPython / PyPy；标准库、NumPy、Numba 等是否允许必须先固定，不能混为纯 Python。
3. 替换编译为语法检查，在容器中执行解释器；继续限制网络、进程、文件、内存和 stdout。
4. 固定是否把解释器启动、导入、JIT 编译算入硬时限。
5. 为语言、解释器版本和依赖建立新缓存键及协议，不能复用 C++ 评测缓存。
6. 重新从题目采样初代；不得为了追平 C++ 分数而偷偷翻译历史优胜源码后声称冷启动。
7. 相同墙钟预算下语言可能显著影响搜索次数；应区分语言效率和模型设计能力。
8. 用独立目录与新版本名称跑实验，保留 v3/v5 C++ 基线，不覆盖历史。

## 14. 给接手 code agent 的工作要求

开始改进前，请先给出：

- 你读到的实际控制流程、最重要的薄弱环节及证据；
- 要检验的一个或少数几个明确假设，而不是同时重写所有层；
- 计划修改的文件、需要补的测试、预算与数据边界；
- 预注册的对照配置和成功标准。

修改时：

- 优先分离算法策略、语言适配、模型传输、评测基础设施，减少相互污染。
- 不直接编辑历史 candidates / best.cpp 之后继续声称模型原创。
- 不覆盖既有 runs，不清除失败，不读取 private 反馈指导同轮搜索。
- 不自动启动付费请求、提高预算或恢复暂停实验；先取得明确授权。
- 不把密钥或含凭据的日志提交到 Git；仓库私有也不例外。
- 改动协议时显式提高版本 / 新建 run；保留 provenance。
- 更改评分器、timer、缓存、选择规则后，先做单元与隔离集成测试。
- 结果报告同时给出训练、验证、private 的含义；未测的项目明确写未测。

建议交付顺序：
**可重现的失败案例 → 最小框架改动 → 不调用模型的回归测试 → 小预算独立实验 → 同协议多次比较 → 冻结后终测。**

这个仓库的目标不是让下一位 agent 继承“某几个优秀矩形算法”，而是让它理解并改进：
**如何可靠地把模型的算法设计能力，转化为持续、可测量、可追溯的程序改进。**

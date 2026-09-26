# 官方 ALE-Bench 评测路径

本项目的纯 Python 本地后端无需第三方包。官方后端需要安装上游及其 Docker 镜像，建议在 Linux/WSL2 中运行；Windows 本地与 Docker 的结果不是官方环境排名。

以下命令在仓库根目录执行。Python 要求 >=3.11。

```bash
python -m pip install 'git+https://github.com/SakanaAI/ALE-Bench.git@3da9b12fb5d112dabb3af693d1a42031c95142bc'
```

随后按 [上游安装文档](https://github.com/SakanaAI/ALE-Bench#setup) 安装系统 Cairo 依赖和构建 202301 judge 镜像（包括工具镜像及 Python 镜像）。仅下载 `python:3.11-slim` 不足以运行 ALE-Bench。上游镜像构建涉及用户 UID/GID，因此遵循其针对当前系统的命令。

```bash
# 仅通过官方公共接口取得 public inputs，不访问私有种子。
python -m aad prepare --source ale-public --output data/ale_public

# 修改 configs/ale.json 中模型选项；密钥通过隐藏输入提供。
python -m aad run --config configs/ale.json --ask-key --finalize

# 对已冻结的最终源码进行官方 public + 一次 private 评测。
python -m aad ale-final runs/ale_deepseek/best.py --output runs/ale_deepseek/official.json --private
```

`AleEvaluator` 的候选评测使用 `session.case_eval`，允许按照分级策略评测指定公共样例；终局才使用 `public_eval` / `private_eval`。本项目使用 `code_language="python"`、`judge_version="202301"`、`lite_version=False`。

CLI 在终局评测前写入源码哈希 receipt；相同输出路径不允许重复启动。上游还在一个 Session 内强制只允许一次 private evaluation。终局结果不回传搜索器。文件级防护不是防作弊平台，用户主动创建新实验仍可重复运行，因此研究报告还应遵守预注册实验规程。

本次交付中若没有 `official.json`，就代表官方 Docker 工具链和私有评测尚未完成，不可引用本地分数作为官方成绩。

## 轻量官方工具交叉验证

不安装整个 ALE-Bench 依赖栈，也可以使用官方数据包自带的 Rust 生成器和计分器，检查 AHC001 公共输入与输出兼容性：

```bash
# 先把原始 ahc001.zip 解压到 data/reference/，保留 ahc001/tools/ 结构。
docker pull rust:1.85-slim
python scripts/official_tools.py build
python scripts/official_tools.py prepare
python scripts/official_tools.py verify --run runs/deepseek --output runs/official_tool_check
```

脚本仅提取元数据中的 `seeds.public`，生成 50 个官方公共输入；不使用 private 字段。交叉验证要求搜索已冻结。所有候选输出由原始 Rust `vis` 再计分，并与独立 Python checker 比较。结果在 `comparison.json`，逐例分数在 `baseline/rust_scores.json` 和 `champion/rust_scores.json`。

这条路径验证的是原题工具兼容性，不执行 ALE-Bench Session，不产生正式排名。

## 完整 1000 例系统集终局测试

用户明确要求终局测试后，可从 [AtCoder 原题](https://atcoder.jp/contests/ahc001/tasks/ahc001_a) 下载 [系统种子压缩包](https://img.atcoder.jp/ahc001/seeds.zip)，解压到 `data/official_system_seeds/`，然后执行：

```bash
python scripts/system_test.py
```

脚本核对 `seeds.txt` 的官方 MD5 `8fc1ce3f4beabac6abc1bdb4206d7f7e`，并要求这 1000 个种子与 ALE-Bench 的 `seeds.private` 数量、内容、顺序完全一致。使用原始 Rust 生成器生成输入后，仅评测已经冻结的 `runs/deepseek` 初始与最终程序，所有合法输出再交给原始 Rust 计分器复核。

输入与种子保存在 `data/official_system_1000/`；完整结果保存在 `runs/system_test_1000/`，其中 `report.md`、`report.json` 为汇总，`per_case.csv` 为逐例比较。评测中断时可再次运行相同命令复用已有逐例缓存；receipt 要求源码哈希、种子哈希与执行配置一致。完成后直接返回，不重新执行或继续优化。

这里使用完整官方系统测试集，但执行环境为本机 Python Docker，未调用官方 Session，也不计算排名；应报告为“完整系统集上的本机原始工具评测”。

### v2 C++ 终局测试

在相同官方输入与 Rust 工具准备完成后：

```bash
docker build -f docker/Dockerfile.cpp -t mosaic-cpp:1 docker
python scripts/v2_system_test.py
```

新机器还需下载 [固定版本的原始数据包](https://huggingface.co/datasets/SakanaAI/ALE-Bench/resolve/0f42617/ahc001.zip)，将其中 `ahc001/` 解压到 `data/reference/`，执行 `python scripts/official_tools.py build`，并准备 `python:3.11-slim` 镜像。交付 ZIP 已包含系统种子及生成后的 1000 个输入；原始工具源码与镜像不在包内。

评测自己的新冻结实验可传 `--run-dir runs/v2_new --output runs/v2_new_system --cache-root runs/v2_new_evaluations`，避免覆盖交付证据。同一目录只允许一个评测进程运行。

默认读取冻结的 `runs/v2_deepseek/best.cpp`，3.5 秒搜索预算、候选进程容器内墙钟 5 秒硬上限、容器外基础设施 30 秒上限、并发 6；输出为 `runs/v2_system_1000_runtime5/`。不会调用 LLM。该脚本不使用上文仅支持 Python 的 `aad ale-final` 路径，也不产生 ALE-Bench Session 排名。此前两次包含容器启停计时的尝试发生超时，记录另行保留，详见 [v2 设计](V2_DESIGN.md)。

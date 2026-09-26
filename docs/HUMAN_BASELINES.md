# AHC001 人类公开程序实测：kusano

完成日期：2026-09-20。使用与 MOSAIC v2 完全相同的 ALE-Bench AHC001 全部 1000 个 private seeds 对应输入。逐例输入哈希和 Docker 镜像一致性已经程序化核验。

## 来源与身份边界

本地 ALE-Bench 数据包包含排行榜分数、原始工具和种子，但没有收录这份人类源码。源码实际来自 [kusano 本人公开仓库](https://github.com/kusano/ahc001)，其 [本人参赛文章](https://qiita.com/kusano_k/items/82fb97b7218fd5871e1c) 链接了该仓库。

固定 Git blob：`96f04fdaafdb5b9c785ee8dcc2061409e22f144a`。下载时验证了 Git blob SHA-1，并保存原始文件 SHA-256：`e21305ca5c67cdd3f58bab59c6cf030162c47f541b33e600133ed8ecb8270899`。原始字节和参与编译的源码哈希一致，没有算法或参数修改。

未核验此版本是否等同于比赛最终提交，也没有将 kusano 标为冠军。本次不是冠军算法复现。其他选手源码未取得，未报告其本机实测成绩。AtCoder 提交页在当前访问环境要求登录；用户确认没有额外源码或链接后，本轮范围限定为 kusano。

## 结果

| 指标 | kusano 公开版本 | MOSAIC v2 既有记录 |
|---|---:|---:|
| 样例数量 | 1000 | 1000 |
| 均分 | 989.413157M | 985.162650M |
| 最低分 | 966.237103M（实例 34） | 949.013896M（实例 649） |
| 最高分 | 998.465287M（实例 672） | 998.889662M（实例 672） |
| AC | 1000/1000 | 1000/1000 |
| 原始 Rust 计分差异 | 0 | 0 |
| 内部搜索预算 | 4.8 秒 | 3.5 秒 |
| 最大候选进程墙钟 | 4.841 秒 | 3.575 秒 |

实例编号从 0 开始。kusano 总分为 **989,413,157,497**，均分比 MOSAIC v2 高 **4.250508M**；逐例胜负为 kusano 849 胜、MOSAIC 151 胜、0 平局。以上是各运行一次的描述性比较，不是显著性结论。

## 比较口径

双方都在同一个 `mosaic-cpp:1` 镜像 ID 下编译运行，使用 `g++ -std=c++17 -O3 -DNDEBUG`，1 CPU、512 MiB、并发 6；容器禁网、只读、非 root。候选进程在容器内受到 5 秒墙钟硬限制，容器外基础设施限时 30 秒，Docker 启停单独计时。kusano 最大容器外耗时 5.687 秒。

保留 kusano 原始代码中的 `limit = 4.8`；其 `int main()` 不读取参数。通用执行器传入的 `4.8` 参数不会修改它的行为。本轮没有把人类源码改为 3.5 秒，也没有重跑或优化 MOSAIC。

因此，这是**相同进程硬时限下、不同内部搜索预算**的比较，不能声称是严格等搜索预算消融，更不是官方标准硬件排名。MOSAIC 记录来自前一次测试，并非本次同时重跑。所有 1000 例均纳入均分；如出现失败，本脚本会计零分，不会剔除。

## 可复核文件

- [来源及原始哈希](../data/human_references/kusano/provenance.json)
- [原始源码](../data/human_references/kusano/original.cpp)
- [机器可读实测报告](../runs/human_kusano_1000/report.json)
- [逐例结果](../runs/human_kusano_1000/per_case.csv)
- [逐例与 MOSAIC 对比](../runs/human_kusano_1000/paired.csv)
- [比较汇总](../runs/human_kusano_1000/comparison.json)
- [Rust 复核](../runs/human_kusano_1000/rust_check/rust_scores.json)

运行 `python scripts/human_system_test.py` 可在同一评测协议下断点续跑；已有完整报告时直接返回，不重新试验或择优。`python scripts/compare_human_ahc001.py` 会重新核验输入与镜像一致性并生成逐例对照。原 v0.2 ZIP 保持不变，本次新增资料保存在工作区上述目录。

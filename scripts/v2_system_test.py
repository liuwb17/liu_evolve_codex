"""Terminal official 1000-case test of the frozen v2 C++ program."""
import argparse
import csv
import statistics
import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aad.cpp import CppEvaluator
from aad.io import digest,read_json,save_json
from aad.runner import summarize
from system_test import prepare,REFERENCE,ROOT
from official_tools import docker

RUN=ROOT/'runs/v2_deepseek'
OUTPUT=ROOT/'runs/v2_system_1000'

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-dir',type=Path,default=ROOT/'runs/v2_deepseek')
    parser.add_argument('--seconds',type=float,default=3.5)
    parser.add_argument('--workers',type=int,default=6)
    parser.add_argument('--output',type=Path,default=ROOT/'runs/v2_system_1000_runtime5')
    parser.add_argument('--cache-root',type=Path,default=ROOT/'runs/v2_runtime5_evaluations')
    args=parser.parse_args()
    RUN=args.run_dir.resolve()
    if not 0<args.seconds<5 or args.workers<1:raise ValueError('Invalid evaluation budget')
    OUTPUT=args.output.resolve()
    state=read_json(RUN/'state.json')
    if not state['frozen']:raise ValueError('Freeze the v2 candidate first')
    if (OUTPUT/'report.json').exists():
        print('Already complete:',OUTPUT/'report.json');return
    source=(RUN/'best.cpp').read_text(encoding='utf-8')
    if digest(source)!=state['champion']:raise ValueError('Frozen source mismatch')
    manifest,cases=prepare()
    OUTPUT.mkdir(parents=True,exist_ok=True)
    receipt={'source_sha256':digest(source),'seeds_sha256':manifest['seeds_sha256'],'search_seconds':args.seconds,'workers':args.workers,
             'timing':'candidate-process-wall-inside-container','timeout_seconds':5.0,'infrastructure_timeout_seconds':30.0}
    if (OUTPUT/'receipt.json').exists() and read_json(OUTPUT/'receipt.json')!=receipt:raise ValueError('Receipt mismatch')
    save_json(OUTPUT/'receipt.json',receipt)
    evaluator=CppEvaluator(args.cache_root,workers=args.workers,seconds=args.seconds,container_timer=True)
    folder=OUTPUT/'champion';folder.mkdir(exist_ok=True)
    rows=[]
    for start in range(0,1000,30):
        rows.extend(evaluator.evaluate(source,cases[start:start+30])['cases'])
        progress={'completed':len(rows),'mean_score':statistics.mean(r['score'] for r in rows),
                  'status_counts':dict(Counter(r['status'] for r in rows))}
        save_json(OUTPUT/'progress.json',progress)
        print(progress,flush=True)
    for i,row in enumerate(rows):
        (folder/f'{i:04d}.in').write_text(cases[i].text,encoding='utf-8')
        (folder/f'{i:04d}.out').write_text(row.get('stdout',''),encoding='utf-8')
    save_json(folder/'local.json',summarize(rows))
    docker(['run','--rm','--network=none','--mount',f"type=bind,source={REFERENCE/'tools'},target=/tools,readonly",
            '--mount',f'type=bind,source={folder},target=/work','--mount',f"type=bind,source={ROOT/'scripts'},target=/scripts,readonly",
            '--workdir=/work','python:3.11-slim','python','/scripts/rust_score_batch.py'])
    official=read_json(folder/'rust_scores.json')
    if len(official)!=1000:raise ValueError('Missing official scores')
    mismatches=[i for i,r in enumerate(rows) if r['status']=='AC' and r['score']!=official[i]['score']]
    scores=[r['score'] for r in rows]
    report={**receipt,'count':1000,'mean_score':statistics.mean(scores),'total_score':sum(scores),
            'normalized_mean':statistics.mean(scores)/1e9,'min_score':min(scores),
            'status_counts':dict(Counter(r['status'] for r in rows)),
            'max_wall_seconds':max(r['seconds'] for r in rows),
            'max_container_wall_seconds':max(r.get('container_wall_seconds',r['seconds']) for r in rows),'rust_mismatches':mismatches,
            'target_mean_score':970000000,'target_met':all(r['status']=='AC' for r in rows) and not mismatches and statistics.mean(scores)>=970000000,
            'candidate_origin':state['records'][state['champion']]['origin'],
            'official_rank_evaluated':False,'cpp_image':evaluator.image}
    save_json(OUTPUT/'report.json',report)
    with (OUTPUT/'per_case.csv').open('w',encoding='utf-8',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['case','seed','score','status','wall_seconds','rust_score'])
        for i,row in enumerate(rows):writer.writerow([i,manifest['cases'][i]['seed'],row['score'],row['status'],row['seconds'],official[i]['score']])
    text=f"# MOSAIC v2 官方 1000 例验收\n\n平均分：{report['mean_score']:,.3f}；总分：{report['total_score']:,}。\n\n状态：{report['status_counts']}。官方 Rust 计分不一致：{len(mismatches)}。最大单例墙钟：{report['max_wall_seconds']:.3f} 秒。\n\n970M 目标达成：{report['target_met']}。候选来源：{report['candidate_origin']}。\n\n程序在新开发集上选定并冻结后测试官方 1000 seeds；没有使用本次系统集输出继续搜索。运行环境为本机 Docker C++17，搜索预算 4.25 秒，单例执行硬上限 5 秒（含容器启动），1 CPU/512 MiB，并发 6。未计算标准环境排名。\n"
    text=text.replace('搜索预算 4.25 秒',f'搜索预算 {args.seconds:g} 秒').replace('并发 6',f'并发 {args.workers}')
    text=text.replace('单例执行硬上限 5 秒（含容器启动）','候选进程墙钟硬上限 5 秒（容器内计时，不含 Docker 启停）；容器外基础设施上限 30 秒')
    text+=f"\n最大容器外总耗时：{report['max_container_wall_seconds']:.3f} 秒。\n"
    text+='\n此前 4.25 秒/并发 6 的运行出现超时，保留在 runs/v2_system_1000；本次统一降低预算，使用独立缓存重测全部样例，未仅重试失败例。源码始终不变。\n'
    text+='\n随后 3.5 秒但包含 Docker 启停的计时尝试也出现超时，保留在 runs/v2_system_1000_final。本报告改为容器内候选进程计时；不声称满足旧的含 Docker 启停 5 秒限制。详见 docs/V2_DESIGN.md。\n'
    (OUTPUT/'report.md').write_text(text,encoding='utf-8')
    print(report,flush=True)
if __name__=='__main__':main()

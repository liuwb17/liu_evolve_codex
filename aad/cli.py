import argparse
import getpass
import json
import os
from pathlib import Path

from .config import Config
from .engine import Evolution
from .problem import prepare_dataset
from .report import finalize


def main():
    parser = argparse.ArgumentParser(description="MOSAIC AAD — AHC001 algorithm evolution")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="Create immutable train/validation/holdout splits")
    prepare.add_argument("--output", type=Path, default=Path("data/ahc001"))
    prepare.add_argument("--count", type=int, default=12)
    prepare.add_argument("--sample", type=Path)
    prepare.add_argument("--source", choices=["synthetic", "ale-public"], default="synthetic")
    run = sub.add_parser("run")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--resume", action="store_true")
    run.add_argument("--finalize", action="store_true")
    run.add_argument("--ask-key", action="store_true", help="Read API key without echo; never write it to disk")
    final = sub.add_parser("finalize")
    final.add_argument("run_dir", type=Path)
    official = sub.add_parser("ale-final")
    official.add_argument("source", type=Path)
    official.add_argument("--output", type=Path, required=True)
    official.add_argument("--workers", type=int, default=2)
    official.add_argument("--private", action="store_true", help="Terminal official private evaluation")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            if args.source == "ale-public":
                from .ale import prepare_public_dataset
                result = prepare_public_dataset(args.output)
            else:
                result = prepare_dataset(args.output, args.count, args.sample)
            print(json.dumps({"kind": result["kind"], "splits": {k: len(v) for k, v in result["splits"].items()}}, indent=2))
        elif args.command == "run":
            config = Config.load(args.config)
            if args.ask_key:
                os.environ[config.api_key_env] = getpass.getpass("AAD API key (hidden): ")
            evolution = Evolution(config, resume=args.resume)
            if config.provider == "chat":
                print("Available models:", evolution.proposer.check_connection(), flush=True)
            evolution.run()
            if args.finalize:
                finalize(Path(config.run_dir))
            print(f"Artifacts: {Path(config.run_dir).resolve()}")
        elif args.command == "finalize":
            report = finalize(args.run_dir)
            print(json.dumps({"holdout_mean": report["champion"]["holdout"]["mean"],
                              "paired": report["paired_holdout"]}, indent=2))
        else:
            from .ale import official_final
            print(json.dumps(official_final(args.source, args.output, args.workers, args.private), indent=2))
    except (ValueError, RuntimeError, FileNotFoundError) as error:
        parser.exit(2, f"Error: {error}\n")

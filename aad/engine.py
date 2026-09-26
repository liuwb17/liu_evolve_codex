"""Sequential proposal orchestration, parallel case evaluation, resumable archives."""

import platform
import random
import sys
import time
from pathlib import Path

from .archive import Archive, OperatorBandit
from .config import Config
from .io import digest, read_json, save_json, tuple_tree
from .offline import OfflineProposer
from .problem import load_split
from .provider import BudgetExhausted, ChatProposer
from .runner import LocalEvaluator


def compact(evaluation):
    if not evaluation:
        return None
    return {**{k: v for k, v in evaluation.items() if k != "cases"},
            "cases": [{k: r.get(k) for k in ("name", "status", "score", "seconds", "message", "worst_ads")}
                      for r in evaluation["cases"]]}


class Evolution:
    def __init__(self, config: Config, resume=False):
        config.validate()
        self.config = config
        self.root = Path(config.run_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"
        self.train = load_split(Path(config.dataset), "train")
        self.validation = load_split(Path(config.dataset), "validation")
        self.seed_code = (Path(__file__).parent / "assets/seed.py").read_text(encoding="utf-8")
        frozen_config = config.as_dict()
        for key in ("iterations", "max_requests", "run_dir"):
            frozen_config.pop(key)
        signature = digest(str(sorted(frozen_config.items())) + self.seed_code
                           + (Path(config.dataset) / "manifest.json").read_text(encoding="utf-8"))
        if self.state_path.exists():
            if not resume:
                raise ValueError("Run exists; use --resume or a new run_dir")
            self.state = read_json(self.state_path)
            if self.state["signature"] != signature:
                raise ValueError("Resume config, seed or dataset differs from the original run")
            if self.state.get("frozen"):
                raise ValueError("Run is frozen for holdout evaluation; start a new experiment")
        else:
            if resume:
                raise ValueError("Cannot resume a missing run")
            self.state = {"version": 1, "signature": signature, "iteration": 0,
                          "candidates": {}, "events": [], "champion": None,
                          "baseline": None, "elapsed_seconds": 0.0, "frozen": False}
        self.rng = random.Random(config.seed)
        if "rng" in self.state:
            self.rng.setstate(tuple_tree(self.state["rng"]))
        self.archive = Archive(config.islands, self.state.get("archive"))
        self.bandit = OperatorBandit(self.state.get("bandit"))
        if config.backend == "ale":
            from .ale import AleEvaluator
            self.evaluator = AleEvaluator(self.root, config)
        else:
            self.evaluator = LocalEvaluator(self.root / "cache", config.backend, config.timeout,
                                            config.workers, config.docker_image)
        self.proposer = ChatProposer(config.as_dict(), self.root) if config.provider == "chat" else OfflineProposer()
        save_json(self.root / "config.json", config.as_dict())
        save_json(self.root / "environment.json", {"python": sys.version, "platform": platform.platform(),
                  "machine": platform.machine(), "processor": platform.processor(),
                  "runtime": getattr(self.evaluator, "runtime", {"backend": "ale"}),
                  "protocol": "MOSAIC v1; fixed train/selection-validation; one final holdout"})

    @property
    def records(self):
        return self.state["candidates"]

    def code(self, candidate_id):
        code = (self.root / "candidates" / candidate_id / "solution.py").read_text(encoding="utf-8")
        if digest(code) != candidate_id:
            raise ValueError("Candidate source hash mismatch")
        return code

    def checkpoint(self):
        self.state.update(rng=self.rng.getstate(), archive=self.archive.cells, bandit=self.bandit.state)
        save_json(self.state_path, self.state)

    def store(self, code, record):
        candidate_id = digest(code)
        record["id"] = candidate_id
        folder = self.root / "candidates" / candidate_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "solution.py").write_text(code, encoding="utf-8")
        save_json(folder / "record.json", record)
        self.records[candidate_id] = record
        return candidate_id

    def initialize(self):
        if self.state["baseline"]:
            return
        train = self.evaluator.evaluate(self.seed_code, self.train)
        validation = self.evaluator.evaluate(self.seed_code, self.validation)
        if not train["valid"] or not validation["valid"]:
            save_json(self.root / "baseline_failure.json", {"train": train, "validation": validation})
            raise RuntimeError("Seed failed; see baseline_failure.json")
        record = {"parents": [], "operator": "seed", "hypothesis": "Human-written balanced expansion seed",
                  "provider": "human-seed", "train": train, "validation": validation, "iteration": -1}
        candidate_id = self.store(self.seed_code, record)
        self.state.update(baseline=candidate_id, champion=candidate_id)
        for island in range(self.config.islands):
            self.archive.add(record, self.records, island)
        self.checkpoint()
        print(f"baseline train={train['mean']:.6f} validation={validation['mean']:.6f}", flush=True)

    def step(self, iteration):
        island = iteration % self.config.islands
        parent_id = self.archive.select(self.records, island, self.rng)
        donor_id = self.archive.select(self.records, (island + 1) % self.config.islands, self.rng)
        parent_code = self.code(parent_id)
        donor_code = self.code(donor_id)
        operator = self.bandit.choose(self.rng)
        feedback = {"parent_train": compact(self.records[parent_id]["train"]),
                    "recent_attempts": self.state["events"][-5:]}
        reward = 0.0
        for attempt in range(self.config.repairs + 1):
            event = {"iteration": iteration, "attempt": attempt, "island": island,
                     "parent": parent_id, "donor": donor_id, "operator": operator}
            try:
                proposal = self.proposer.propose(parent_code, donor_code, operator, feedback, self.rng)
            except BudgetExhausted:
                self.state["stop_reason"] = "request_budget"
                return False
            except (ValueError, SyntaxError, RuntimeError) as error:
                event.update(status="proposal_error", message=str(error)[:2000])
                self.state["events"].append(event)
                feedback["repair"] = str(error)[:2000]
                self.checkpoint()
                continue
            code = proposal.pop("code")
            candidate_id = digest(code)
            event["candidate"] = candidate_id
            event["hypothesis"] = proposal["hypothesis"]
            if candidate_id in self.records:
                event["status"] = "duplicate"
                self.state["events"].append(event)
                break
            smoke = self.evaluator.evaluate(code, self.train[:self.config.smoke_cases])
            record = {**proposal, **event, "parents": [parent_id, donor_id], "smoke": smoke,
                      "train": None, "validation": None}
            if not smoke["valid"]:
                record["status"] = event["status"] = "invalid"
                self.store(code, record)
                self.state["events"].append(event)
                feedback["failed_source"] = code
                feedback["repair"] = compact(smoke)
                self.checkpoint()
                continue
            # Compare the same case subset, not a subset mean against a full-set mean.
            parent_smoke = self.evaluator.evaluate(self.code(parent_id), self.train[:self.config.smoke_cases])
            if (smoke["mean"] < parent_smoke["mean"] - self.config.screen_margin
                    and self.rng.random() >= self.config.screen_exploration):
                record["status"] = event["status"] = "screened"
                self.store(code, record)
                self.state["events"].append(event)
                break
            train = self.evaluator.evaluate(code, self.train)
            validation = self.evaluator.evaluate(code, self.validation) if train["valid"] else None
            record.update(train=train, validation=validation)
            valid = train["valid"] and validation and validation["valid"]
            event["status"] = record["status"] = "evaluated" if valid else "invalid"
            event["train_mean"] = train["mean"]
            if validation:
                event["validation_mean"] = validation["mean"]
            self.store(code, record)
            self.state["events"].append(event)
            if valid:
                novel = self.archive.add(record, self.records, island)
                parent_score = self.records[parent_id]["train"]["selection_score"]
                reward = min(1.0, max(0.0, train["selection_score"] - parent_score) * 10 + 0.1 * novel)
                champion = self.records[self.state["champion"]]
                if validation["selection_score"] > champion["validation"]["selection_score"]:
                    self.state["champion"] = candidate_id
                break
            feedback["failed_source"] = code
            feedback["repair"] = compact(validation if validation and not validation["valid"] else train)
            self.checkpoint()
        self.bandit.update(operator, reward)
        if (iteration + 1) % self.config.migration_interval == 0:
            self.archive.migrate(self.records)
        champion = self.records[self.state["champion"]]
        print(f"iteration={iteration+1} operator={operator} best_validation={champion['validation']['mean']:.6f} candidates={len(self.records)}", flush=True)
        return True

    def run(self):
        started = time.monotonic()
        try:
            self.initialize()
            for iteration in range(self.state["iteration"], self.config.iterations):
                if not self.step(iteration):
                    break
                self.state["iteration"] = iteration + 1
                self.checkpoint()
            self.export()
            return self.state
        finally:
            self.state["elapsed_seconds"] += time.monotonic() - started
            self.checkpoint()
            if hasattr(self.evaluator, "close"):
                self.evaluator.close()

    def export(self):
        champion = self.state["champion"]
        if champion:
            (self.root / "best.py").write_text(self.code(champion), encoding="utf-8")
            save_json(self.root / "best.json", {"id": champion,
                      "train": compact(self.records[champion]["train"]),
                      "validation": compact(self.records[champion]["validation"]),
                      "holdout_evaluated": False, "official_private_evaluated": False})

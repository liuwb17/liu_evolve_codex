import json
import random
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aad.archive import Archive, OperatorBandit
from aad.ale import AleEvaluator
from aad.blocks import apply_blocks, extract
from aad.config import Config
from aad.engine import Evolution
from aad.io import read_json
from aad.offline import OfflineProposer
from aad.problem import Case, judge, load_split, parse_input, prepare_dataset, synthetic_case
from aad.provider import BudgetExhausted, ChatProposer, decode_json
from aad.report import finalize
from aad.runner import LocalEvaluator, execute

SEED = (Path(__file__).resolve().parents[1] / "aad/assets/seed.py").read_text(encoding="utf-8")


class JudgeTests(unittest.TestCase):
    def test_exact_touching_and_oversize(self):
        text = "2\n0 0 100\n10 0 100\n"
        self.assertEqual(judge(text, "0 0 10 10\n10 0 20 10")["score"], 1_000_000_000)
        self.assertEqual(judge("1\n0 0 100\n", "0 0 20 10")["score"], 750_000_000)

    def test_missing_anchor_is_zero_not_wa(self):
        self.assertEqual(judge("1\n0 0 100\n", "1 1 11 11")["status"], "AC")
        self.assertEqual(judge("1\n0 0 100\n", "1 1 11 11")["score"], 0)

    def test_invalid_outputs(self):
        text = "2\n0 0 10\n1 1 10\n"
        for output in ("", "0 0 2 2\n1 1 3 3", "0 0 0 1\n1 1 2 2",
                       "-1 0 1 1\n1 1 2 2", "0.0 0 1 1\n1 1 2 2",
                       "0 0 1 1\n1 1 10001 2", "0 0 1 1\n1 1 2 2\n0"):
            self.assertEqual(judge(text, output)["status"], "WA", output)

    def test_overlap_even_if_anchor_missing(self):
        self.assertEqual(judge("2\n0 0 10\n9 9 10\n", "1 1 4 4\n2 2 5 5")["status"], "WA")

    def test_synthetic_distribution_constraints(self):
        for seed in range(20):
            points = parse_input(synthetic_case(seed))
            self.assertTrue(50 <= len(points) <= 200)
            self.assertEqual(sum(p[2] for p in points), 100_000_000)


class GenomeTests(unittest.TestCase):
    def test_unknown_blocks_and_syntax_rejected(self):
        for edit in ({"unknown": "x=1"}, {"search": "# AAD-END search"}, {"priority": "def broken("}):
            with self.assertRaises((ValueError, SyntaxError)):
                apply_blocks(SEED, edit)

    def test_skeleton_immutable(self):
        changed = apply_blocks(SEED, {"parameters": "ROUNDS = 2\n"})
        self.assertEqual(extract(changed)["search"], extract(SEED)["search"])
        self.assertEqual(changed[:changed.index("# AAD-BEGIN")], SEED[:SEED.index("# AAD-BEGIN")])

    def test_offline_all_operators_parse(self):
        proposer = OfflineProposer()
        for op in OperatorBandit.names:
            result = proposer.propose(SEED, SEED, op, {}, random.Random(4))
            self.assertEqual(set(extract(result["code"])), {"parameters", "priority", "search"})


class ExecutionTests(unittest.TestCase):
    def test_timeout_when_process_never_reads_large_input(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            result = execute([sys.executable, "-c", "while True: pass"], "x" * 200000,
                             Path(tmp), 0.2)
            self.assertEqual(result["status"], "TLE")

    def test_seed_scored_externally_and_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            evaluator = LocalEvaluator(Path(tmp), workers=1)
            case = Case("one", synthetic_case(7, n=50))
            first = evaluator.evaluate(SEED, [case])
            second = evaluator.evaluate(SEED, [case])
            self.assertTrue(first["valid"])
            self.assertEqual(first, second)
            self.assertEqual(evaluator.executions, 1)
            self.assertEqual(evaluator.hits, 1)

    def test_timeout_runtime_and_output_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            evaluator = LocalEvaluator(Path(tmp), timeout=0.35, workers=1)
            case = Case("one", "1\n0 0 1\n")
            for source, status in [("while True: pass", "TLE"), ("raise RuntimeError('test')", "RE"),
                                   ("print('x' * 100000)", "OLE"), ("def (", "CE")]:
                result = evaluator.evaluate(source, [case])
                self.assertEqual(result["cases"][0]["status"], status)
                self.assertFalse(result["valid"])

    def test_credentials_not_in_child_environment(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"AAD_API_KEY": "unit-test-secret"}):
            evaluator = LocalEvaluator(Path(tmp), workers=1)
            result = evaluator.evaluate("import os\nassert 'AAD_API_KEY' not in os.environ\nprint('0 0 1 1')", [Case("one", "1\n0 0 1\n")])
            self.assertTrue(result["valid"])


class ProviderTests(unittest.TestCase):
    def test_json_protocol(self):
        self.assertEqual(decode_json('```json\n{"hypothesis":"x","blocks":{"search":"x"}}\n```')["hypothesis"], "x")
        for bad in ('{"hypothesis":1}', '[]', 'not json'):
            with self.assertRaises(ValueError):
                decode_json(bad)

    def test_no_unsafe_default(self):
        with self.assertRaises(ValueError):
            Config(provider="chat", model="unit-test", base_url="https://example.invalid").validate()

    def test_failed_http_consumes_budget(self):
        import urllib.error
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"AAD_API_KEY": "unit-test-secret"}):
            config = Config(provider="chat", backend="docker", base_url="https://example.invalid", model="test", max_requests=1).as_dict()
            proposer = ChatProposer(config, Path(tmp))
            with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)):
                with self.assertRaises(RuntimeError):
                    proposer.propose(SEED, SEED, "search", {}, random.Random(0))
            self.assertEqual(proposer.requests, 1)
            self.assertEqual(read_json(Path(tmp) / "llm/ledger.json")[0]["status"], "http_401")


class IntegrationContractTests(unittest.TestCase):
    def test_upstream_enum_translation_and_independent_score(self):
        case = Case("example", "1\n0 0 100\n")
        item = SimpleNamespace(judge_result=SimpleNamespace(value="ACCEPTED"), absolute_score=1000000000,
                               execution_time=0.1, message="", output_str="0 0 10 10", error_str="")
        adapter = AleEvaluator.__new__(AleEvaluator)
        adapter.config = Config()
        adapter.session = SimpleNamespace(case_eval=lambda *args, **kwargs: SimpleNamespace(case_results=[item]))
        self.assertTrue(adapter.evaluate(SEED, [case])["valid"])
        item.absolute_score = 1
        mismatch = adapter.evaluate(SEED, [case])
        self.assertFalse(mismatch["valid"])
        self.assertEqual(mismatch["cases"][0]["status"], "SCORER_MISMATCH")

    def test_http_contract_ledger_and_budget(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, limit):
                return json.dumps({"choices": [{"message": {"content": json.dumps({"hypothesis": "test", "blocks": {"priority": extract(SEED)["priority"]}})}}], "usage": {"total_tokens": 10}}).encode()

        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"AAD_API_KEY": "unit-test-secret"}):
            config = Config(provider="chat", backend="docker", base_url="https://example.invalid/v1", model="test", max_requests=1).as_dict()
            proposer = ChatProposer(config, Path(tmp))
            with patch("urllib.request.urlopen", return_value=Response()) as mock:
                proposer.propose(SEED, SEED, "priority", {}, random.Random(0))
                request = mock.call_args.args[0]
                self.assertEqual(request.full_url, "https://example.invalid/v1/chat/completions")
                self.assertIn("Authorization", request.headers)
            with self.assertRaises(BudgetExhausted):
                proposer.propose(SEED, SEED, "search", {}, random.Random(0))
            for path in Path(tmp).rglob("*.json"):
                self.assertNotIn("unit-test-secret", path.read_text())


class LifecycleTests(unittest.TestCase):
    def test_resume_equivalence_and_holdout_freeze(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_dataset(root / "data", count=2)
            cfg = Config(dataset=str(root / "data"), run_dir=str(root / "resumed"), iterations=2, workers=1)
            Evolution(cfg).run()
            cfg.iterations = 4
            resumed = Evolution(cfg, resume=True).run()
            cfg.run_dir = str(root / "full")
            full = Evolution(cfg).run()
            self.assertEqual(resumed["champion"], full["champion"])
            self.assertEqual(list(resumed["candidates"]), list(full["candidates"]))
            self.assertEqual(resumed["archive"], full["archive"])
            report = finalize(root / "full")
            self.assertTrue(report["champion"]["holdout"]["valid"])
            self.assertEqual(finalize(root / "full"), report)
            with self.assertRaises(ValueError):
                Evolution(cfg, resume=True)

    def test_manifest_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_dataset(root, count=2)
            path = root / "train/synthetic_00000.txt"
            path.write_text("changed")
            with self.assertRaises(ValueError):
                load_split(root, "train")

    def test_archive_never_accepts_invalid(self):
        archive = Archive(1)
        self.assertFalse(archive.add({"id": "bad", "train": {"valid": False}}, {}, 0))
        self.assertEqual(archive.cells, [{}])


if __name__ == "__main__":
    unittest.main()

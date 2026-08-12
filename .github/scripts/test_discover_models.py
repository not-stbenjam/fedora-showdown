#!/usr/bin/env python3

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import discover_models
from performance_guardrails import PerformanceAudit


def model_metadata(reasoning=None):
    model = {
        "id": "example/model",
        "name": "Example Model",
        "supported_parameters": ["max_tokens", "reasoning", "structured_outputs"],
        "architecture": {"input_modalities": ["text"]},
        "pricing": {"prompt": "0.000001", "completion": "0.000002"},
        "context_length": 32_768,
        "top_provider": {"max_completion_tokens": 8_192},
    }
    if reasoning is not None:
        model["reasoning"] = reasoning
    return model


def variant(slug, effort, warning=()):
    return {
        "slug": slug,
        "effort": effort,
        "content": "<!doctype html><html></html>",
        "performance_audit": PerformanceAudit(tuple(warning)),
    }


class ModelDiscoveryTest(unittest.TestCase):
    def test_uses_only_advertised_efforts_in_canonical_order(self):
        model = model_metadata({
            "supported_efforts": ["low", "max", "medium"],
            "mandatory": False,
        })

        self.assertEqual(
            ["max", "medium", "low"],
            discover_models.get_effort_levels(model),
        )

    def test_null_efforts_mean_all_gateway_efforts(self):
        model = model_metadata({"supported_efforts": None, "mandatory": True})

        self.assertEqual(
            list(discover_models.OPENROUTER_EFFORTS[:-1]),
            discover_models.get_effort_levels(model),
        )

    def test_missing_effort_metadata_keeps_one_default_run(self):
        self.assertEqual([None], discover_models.get_effort_levels(model_metadata()))

    def test_maps_openrouter_none_to_pi_off(self):
        command = discover_models.build_pi_command(
            "example/model", "none", "Build the page"
        )

        thinking_index = command.index("--thinking")
        self.assertEqual("off", command[thinking_index + 1])

    def test_writes_model_specific_thinking_level_map(self):
        model = model_metadata({
            "supported_efforts": ["max", "low", "none"],
            "mandatory": False,
        })

        with tempfile.TemporaryDirectory() as temp:
            discover_models.write_pi_model_config(model, Path(temp))
            definition = json.loads((Path(temp) / "models.json").read_text())[
                "providers"
            ]["openrouter"]["models"][0]

        self.assertEqual(
            {
                "off": "none",
                "minimal": None,
                "low": "low",
                "medium": None,
                "high": None,
                "xhigh": None,
                "max": "max",
            },
            definition["thinkingLevelMap"],
        )

    def test_incremental_registry_updates_keep_family_efforts_together(self):
        initial = """\
const MODELS = [
    { id: "existing", name: "Existing", group: "Example" },
];
"""
        with tempfile.TemporaryDirectory() as temp:
            index = Path(temp) / "index.html"
            index.write_text(initial)
            with mock.patch.object(discover_models, "INDEX_HTML", index):
                discover_models.update_models_array(
                    "example", "Example", "Example", "example/model",
                    [variant("example-max", "max")],
                )
                discover_models.update_models_array(
                    "example", "Example", "Example", "example/model",
                    [variant("example-low", "low")],
                )
            content = index.read_text()

        self.assertLess(content.index('id: "example-max"'), content.index('id: "example-low"'))
        self.assertLess(content.index('id: "example-low"'), content.index('id: "existing"'))

    def test_generates_one_pi_run_per_effort_and_materializes_after_success(self):
        efforts = ["max", "high", "low"]

        def fake_run(_model, slug, effort, _work_dir):
            return variant(slug, effort)

        with tempfile.TemporaryDirectory() as temp:
            repo_root = Path(temp)
            with (
                mock.patch.object(discover_models, "REPO_ROOT", repo_root),
                mock.patch.object(discover_models, "run_pi", side_effect=fake_run) as run,
            ):
                variants, failed_efforts = discover_models.generate_variants(
                    model_metadata(), "example", efforts
                )

            self.assertEqual(3, run.call_count)
            self.assertEqual(efforts, [item["effort"] for item in variants])
            self.assertEqual([], failed_efforts)
            for effort in efforts:
                self.assertTrue((repo_root / f"example-{effort}" / "index.html").is_file())

    def test_preserves_partial_family_when_an_effort_fails(self):
        def fake_run(_model, slug, effort, _work_dir):
            return None if effort == "low" else variant(slug, effort)

        with tempfile.TemporaryDirectory() as temp:
            repo_root = Path(temp)
            with (
                mock.patch.object(discover_models, "REPO_ROOT", repo_root),
                mock.patch.object(discover_models, "run_pi", side_effect=fake_run),
            ):
                variants, failed_efforts = discover_models.generate_variants(
                    model_metadata(), "example", ["high", "low"]
                )

            self.assertEqual(["high"], [item["effort"] for item in variants])
            self.assertEqual(["low"], failed_efforts)
            self.assertTrue((repo_root / "example-high" / "index.html").is_file())
            self.assertFalse((repo_root / "example-low").exists())

    def test_creates_one_commit_per_effort_in_one_pr(self):
        variants = [variant("example-max", "max"), variant("example-low", "low")]
        completed = SimpleNamespace(returncode=0, stdout="value\n", stderr="")

        with (
            mock.patch.object(discover_models, "pick_branch_name", return_value="bot/add-example"),
            mock.patch.object(discover_models, "update_models_array") as update,
            mock.patch.object(discover_models.subprocess, "run", return_value=completed) as run,
        ):
            created = discover_models.create_pr(
                "example", "Example", "Example Group", "example/model", variants,
                failed_efforts=["medium"],
            )

        self.assertTrue(created)
        self.assertEqual(2, update.call_count)
        commit_commands = [
            call.args[0]
            for call in run.call_args_list
            if call.args[0][:2] == ["git", "commit"]
        ]
        self.assertEqual(
            [
                ["git", "commit", "-m", "Add Example at max effort (Example Group)"],
                ["git", "commit", "-m", "Add Example at low effort (Example Group)"],
            ],
            commit_commands,
        )
        pr_commands = [
            call.args[0]
            for call in run.call_args_list
            if call.args[0][:3] == ["gh", "pr", "create"]
        ]
        self.assertEqual(1, len(pr_commands))
        pr_body = pr_commands[0][pr_commands[0].index("--body") + 1]
        self.assertIn("Failed effort levels (not included): `medium`", pr_body)
        self.assertIn("pi-workdirs", pr_body)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3

import re
import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from model_families import (
    family_id,
    family_matches_query,
    family_members,
    flatten_nav_order,
    group_section_indices,
    should_group_family,
)


REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")
GPT56_FAMILIES = {
    "gpt-5.6-sol": "GPT-5.6 Sol",
    "gpt-5.6-terra": "GPT-5.6 Terra",
    "gpt-5.6-luna": "GPT-5.6 Luna",
}


def parse_models(index_html: str) -> list[dict]:
    match = re.search(r"const MODELS = \[(.*?)\];", index_html, re.S)
    if not match:
        raise AssertionError("MODELS array not found")
    body = match.group(1)
    models = []
    depth = 0
    start = None
    for index, char in enumerate(body):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start is not None:
                chunk = body[start + 1 : index]
                model = {}
                for key in ("id", "name", "group", "badge", "family", "dateAdded"):
                    found = re.search(rf'\b{key}\s*:\s*"([^"]*)"', chunk)
                    if found:
                        model[key] = found.group(1)
                if "perfWarning" in chunk:
                    model["perfWarning"] = True
                if "id" in model:
                    models.append(model)
                start = None
    return models


class ModelFamiliesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index_html = (REPO_ROOT / "index.html").read_text()
        cls.models = parse_models(cls.index_html)

    def test_groups_same_family_into_single_disclosure_entry(self):
        models = [
            {"id": "a-max", "name": "Model A", "family": "model-a", "badge": "Max"},
            {"id": "a-low", "name": "Model A", "family": "model-a", "badge": "Low"},
            {"id": "b", "name": "Model B"},
        ]

        entries = group_section_indices(models, range(len(models)))

        self.assertEqual(
            entries,
            [
                {"type": "family", "family": "model-a", "indices": [0, 1]},
                {"type": "model", "index": 2},
            ],
        )

    def test_does_not_group_singleton_or_missing_family(self):
        models = [
            {"id": "solo", "name": "Solo", "family": "only-one"},
            {"id": "plain", "name": "Plain"},
        ]

        self.assertFalse(should_group_family(models, "only-one"))
        self.assertEqual(
            group_section_indices(models, [0, 1]),
            [{"type": "model", "index": 0}, {"type": "model", "index": 1}],
        )

    def test_search_keeps_matching_variants_inside_family(self):
        models = [
            {"id": "a-max", "name": "Model A", "family": "model-a", "badge": "Max", "group": "OpenAI"},
            {"id": "a-xhigh", "name": "Model A", "family": "model-a", "badge": "XHigh", "group": "OpenAI"},
            {"id": "a-low", "name": "Model A", "family": "model-a", "badge": "Low", "group": "OpenAI"},
        ]

        matched = family_matches_query(models, [0, 1, 2], "xhigh")

        self.assertEqual(matched, [1])

    def test_nav_order_includes_collapsed_family_members(self):
        entries = [
            {"type": "family", "family": "model-a", "indices": [4, 5, 6]},
            {"type": "model", "index": 7},
        ]

        self.assertEqual(flatten_nav_order(entries), [4, 5, 6, 7])

    def test_registry_declares_terra_and_luna_reasoning_ladder(self):
        by_id = {model["id"]: model for model in self.models}

        for family, display_name in GPT56_FAMILIES.items():
            with self.subTest(family=family):
                default = by_id.get(family)
                self.assertIsNotNone(default, f"missing default id {family}")
                self.assertEqual(default["name"], display_name)
                self.assertEqual(default.get("badge"), "Default")
                self.assertEqual(family_id(default), family)

                for effort in REASONING_EFFORTS:
                    slug = f"{family}-{effort}"
                    model = by_id.get(slug)
                    self.assertIsNotNone(model, f"missing {slug}")
                    self.assertEqual(model["name"], display_name)
                    self.assertEqual(model.get("badge"), effort.capitalize() if effort != "xhigh" else "XHigh")
                    self.assertEqual(family_id(model), family)
                    self.assertTrue((REPO_ROOT / slug / "index.html").is_file())

                members = family_members(self.models, family)
                self.assertGreaterEqual(len(members), 7)
                self.assertTrue(should_group_family(self.models, family))

        sol_ultra = by_id.get("gpt-5.6-sol-ultra")
        self.assertIsNotNone(sol_ultra)
        self.assertEqual(sol_ultra.get("badge"), "Ultra")
        self.assertEqual(family_id(sol_ultra), "gpt-5.6-sol")
        self.assertNotIn("gpt-5.6-terra-ultra", by_id)
        self.assertNotIn("gpt-5.6-luna-ultra", by_id)

    def test_openai_section_groups_each_gpt56_family(self):
        openai_indices = [
            index
            for index, model in enumerate(self.models)
            if model.get("group") == "OpenAI"
        ]
        entries = group_section_indices(self.models, openai_indices)
        family_entries = {
            entry["family"]: entry["indices"]
            for entry in entries
            if entry["type"] == "family"
        }

        for family in GPT56_FAMILIES:
            with self.subTest(family=family):
                self.assertIn(family, family_entries)
                self.assertGreaterEqual(len(family_entries[family]), 7)

        # Non-family OpenAI models remain flat entries.
        flat_ids = [
            self.models[entry["index"]]["id"]
            for entry in entries
            if entry["type"] == "model"
        ]
        self.assertIn("gpt-5.5", flat_ids)


if __name__ == "__main__":
    unittest.main()

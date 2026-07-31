#!/usr/bin/env python3

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from performance_guardrails import audit_html, find_unguarded_models, warning_message


class PerformanceGuardrailsTest(unittest.TestCase):
    def read_model(self, slug):
        return (REPO_ROOT / slug / "index.html").read_text()

    def test_flags_per_particle_canvas_shadows(self):
        for slug in ("gpt-5.6-sol-none", "qwen3.7-flash", "gemini-3.1-pro"):
            with self.subTest(slug=slug):
                result = audit_html(self.read_model(slug))
                self.assertTrue(result.requires_warning)
                self.assertIn("per-particle canvas shadows", result.reasons)

    def test_does_not_flag_lower_cost_particle_rendering(self):
        result = audit_html(self.read_model("gpt-5.6-sol-low"))

        self.assertFalse(result.requires_warning)
        self.assertEqual((), result.reasons)

    def test_flags_large_sorted_shadow_animation_after_identifier_changes(self):
        content = self.read_model("gpt-5.6-sol-none")
        content = content.replace("count", "budget")
        content = content.replace(
            "for (const { p, q } of visible) {",
            "for (let index = 0; index < dots.length; index++) {",
        )

        result = audit_html(content)

        self.assertTrue(result.requires_warning)
        self.assertIn("large sorted animation with canvas shadows", result.reasons)

    def test_builds_a_user_facing_warning_from_audit_reasons(self):
        result = audit_html(self.read_model("gpt-5.6-sol-none"))

        message = warning_message(result)

        self.assertIn("per-particle canvas shadows", message)
        self.assertIn("freeze or crash", message)

    def test_all_risky_models_have_preload_warnings(self):
        model_paths = [
            REPO_ROOT / "gpt-5.6-sol-none" / "index.html",
            REPO_ROOT / "qwen3.7-flash" / "index.html",
            REPO_ROOT / "gemini-3.1-pro" / "index.html",
        ]
        index_html = (REPO_ROOT / "index.html").read_text()

        violations = find_unguarded_models(model_paths, index_html)

        self.assertEqual([], violations)

    def test_accepts_multiline_model_entry_warning(self):
        with tempfile.TemporaryDirectory() as temp:
            model_dir = Path(temp) / "risky-model"
            model_dir.mkdir()
            model_path = model_dir / "index.html"
            model_path.write_text(self.read_model("gpt-5.6-sol-none"))
            index_html = """\
const MODELS = [
  {
    id: "risky-model",
    name: "Risky Model",
    perfWarning: "Requires confirmation"
  },
];
"""

            violations = find_unguarded_models([model_path], index_html)

        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()

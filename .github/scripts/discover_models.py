#!/usr/bin/env python3
"""Discover new models on OpenRouter, generate fedora entries with Pi, and open PRs."""

import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import requests

OPENROUTER_API = "https://openrouter.ai/api/v1/models"
REPO_ROOT = Path(os.environ.get("GITHUB_WORKSPACE", Path(__file__).resolve().parents[2]))
GROUPS_FILE = REPO_ROOT / ".github" / "scripts" / "provider_groups.json"
INDEX_HTML = REPO_ROOT / "index.html"
PROMPT_FILE = REPO_ROOT / "PROMPT.md"

MAX_MODELS = int(os.environ.get("MAX_MODELS", "5"))
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
MODEL_FILTER = os.environ.get("MODEL_FILTER", "").strip()
PI_TIMEOUT = 600
MIN_HTML_SIZE = 1024
LOOKBACK_SECONDS = 30 * 24 * 3600

SKIP_PROVIDERS = {
    "anthracite-org", "cognitivecomputations", "gryphe", "mancer",
    "nousresearch", "sao10k", "thedrummer", "undi95",
    "openrouter", "switchpoint",
}

SKIP_NAME_PATTERNS = [
    "guard", "shield", "safety", "safeguard", "moderat", "filter",
    "content-safety",
]

SLUG_OVERRIDES = {
    "anthropic/claude-fable-5": "fable-5",
    "anthropic/claude-opus-4": "opus-4",
    "anthropic/claude-opus-4.1": "opus-4-1",
    "anthropic/claude-opus-4.5": "opus-4-5",
    "anthropic/claude-opus-4.6": "opus-4-6",
    "anthropic/claude-opus-4.7": "opus-4-7",
    "anthropic/claude-opus-4.8": "opus-4-8",
    "anthropic/claude-haiku-4.5": "haiku-4-5",
    "anthropic/claude-sonnet-4": "sonnet-4",
    "anthropic/claude-sonnet-4.5": "sonnet-4-5",
    "anthropic/claude-sonnet-4.6": "sonnet-4-6",
    "anthropic/claude-sonnet-5": "sonnet-5",
    "nvidia/nemotron-3-super-120b-a12b": "nemotron-3-super",
    "nvidia/nemotron-3-ultra-550b-a55b": "nemotron-3-ultra",
    "google/gemma-4-26b-a4b-it": "gemma-4-26b",
    "google/gemma-4-31b-it": "gemma-4-31b",
    "google/gemini-3.1-pro-preview": "gemini-3.1-pro",
    "qwen/qwen3-235b-a22b": "qwen3-235b",
    "qwen/qwen3.5-397b-a17b": "qwen3.5-397b",
    "qwen/qwen3.5-27b": "qwen3.5-27b",
}


def get_cost_ceiling(models):
    """Use Sonnet 5's completion price as the cost ceiling."""
    for m in models:
        if m["id"] == "anthropic/claude-sonnet-5":
            price = float(m["pricing"].get("completion") or "0")
            logging.info(
                f"Cost ceiling: ${price * 1_000_000:.2f}/1M completion tokens "
                f"(from anthropic/claude-sonnet-5)"
            )
            return price
    return 0.00001


def should_include(model, cost_ceiling):
    mid = model["id"]
    provider = mid.split("/")[0]

    if mid.endswith(":free") or mid.endswith(":thinking") or mid.endswith("-fast"):
        return False
    if mid.startswith("~"):
        return False
    if provider in SKIP_PROVIDERS:
        return False

    lower_id = mid.lower()
    if any(p in lower_id for p in SKIP_NAME_PATTERNS):
        return False
    if any(x in lower_id for x in ["-vl-", "-vl/", "/vl-", "-vision"]):
        return False
    if lower_id.endswith("-vl"):
        return False
    if any(x in lower_id for x in ["search", "deep-research", "ui-tars"]):
        return False
    if any(x in lower_id for x in ["relace-apply", "relace-search"]):
        return False

    if model.get("context_length", 0) < 4096:
        return False

    out_mods = model.get("architecture", {}).get("output_modalities", [])
    if out_mods != ["text"]:
        return False

    completion_price = float(model.get("pricing", {}).get("completion") or "0")
    if completion_price > cost_ceiling:
        return False

    return True


def generate_slug(model_id):
    if model_id in SLUG_OVERRIDES:
        return SLUG_OVERRIDES[model_id]

    provider, model_part = model_id.split("/", 1)
    slug = model_part

    if provider == "anthropic":
        slug = re.sub(r"^claude-", "", slug)
        slug = slug.replace(".", "-")
        return slug

    slug = re.sub(r"-instruct$", "", slug)
    slug = re.sub(r"-it$", "", slug)
    slug = re.sub(r"-preview$", "", slug)
    slug = re.sub(r"-\d+b-a\d+b$", "", slug)
    slug = re.sub(r"-a\d+b$", "", slug)

    return slug


def generate_display_name(model):
    name = model["name"]
    if ": " in name:
        name = name.split(": ", 1)[1]

    provider = model["id"].split("/")[0]
    if provider == "anthropic":
        name = re.sub(r"^Claude\s+", "", name)

    name = re.sub(r"\s*\(free\)$", "", name, flags=re.IGNORECASE)
    return name.strip()


def get_existing_openrouter_ids():
    """Extract all openrouterId values already in index.html."""
    content = INDEX_HTML.read_text()
    return set(re.findall(r'openrouterId:\s*"([^"]+)"', content))


def has_open_pr(slug):
    result = subprocess.run(
        ["gh", "pr", "list", "--state", "open",
         "--search", f"Add {slug} in:title", "--json", "number"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    if result.returncode == 0:
        prs = json.loads(result.stdout)
        return len(prs) > 0
    return False


def run_pi(model_id, slug, work_dir):
    prompt = PROMPT_FILE.read_text().strip()

    cmd = [
        "pi",
        "--provider", "openrouter",
        "--model", model_id,
        "--no-context-files", "--no-extensions",
        "--no-skills", "--no-themes", "--no-session",
        "-p", prompt,
    ]

    logging.info(f"Running Pi with model {model_id} in {work_dir}")

    try:
        result = subprocess.run(
            cmd, cwd=work_dir, timeout=PI_TIMEOUT,
            capture_output=True, text=True,
        )
        logging.info(f"Pi exit code: {result.returncode}")
        if result.stdout:
            logging.debug(f"Pi stdout (last 500): {result.stdout[-500:]}")
        if result.stderr:
            logging.debug(f"Pi stderr (last 500): {result.stderr[-500:]}")
    except subprocess.TimeoutExpired:
        logging.error(f"Pi timed out after {PI_TIMEOUT}s for {model_id}")
        return False
    except Exception as e:
        logging.error(f"Pi failed for {model_id}: {e}")
        return False

    output_file = work_dir / "index.html"
    if not output_file.exists():
        candidates = list(work_dir.rglob("index.html"))
        if candidates:
            output_file = candidates[0]
            logging.info(f"Found index.html at {output_file}")
        else:
            logging.error(f"No index.html produced for {model_id}")
            return False

    size = output_file.stat().st_size
    if size < MIN_HTML_SIZE:
        logging.error(f"index.html too small ({size} bytes) for {model_id}")
        return False

    content = output_file.read_text(errors="replace")
    if not re.search(r"<!DOCTYPE|<html", content, re.IGNORECASE):
        logging.error(f"index.html doesn't look like valid HTML for {model_id}")
        return False

    dest = REPO_ROOT / slug
    dest.mkdir(exist_ok=True)
    (dest / "index.html").write_text(content)

    logging.info(f"Generated {slug}/index.html ({size} bytes)")
    return True


def update_models_array(slug, display_name, group, openrouter_id):
    lines = INDEX_HTML.read_text().splitlines(keepends=True)

    array_start = None
    array_end = None
    for i, line in enumerate(lines):
        if "const MODELS = [" in line:
            array_start = i
        if array_start is not None and line.strip() == "];":
            array_end = i
            break

    if array_start is None or array_end is None:
        raise RuntimeError("Could not find MODELS array in index.html")

    new_entry = (
        f'    {{ id: "{slug}", name: "{display_name}", '
        f'group: "{group}", openrouterId: "{openrouter_id}" }},\n'
    )

    first_group_line = None
    for i in range(array_start + 1, array_end):
        if f'group: "{group}"' in lines[i]:
            first_group_line = i
            break

    if first_group_line is not None:
        lines.insert(first_group_line, new_entry)
    else:
        lines.insert(array_end, new_entry)

    INDEX_HTML.write_text("".join(lines))
    logging.info(f"Added {slug} to MODELS array in group {group}")


def create_pr(slug, display_name, group, model_id):
    branch = f"bot/add-{slug}"

    subprocess.run(
        ["git", "checkout", "-b", branch, "main"],
        cwd=REPO_ROOT, check=True,
    )
    subprocess.run(
        ["git", "add", f"{slug}/index.html", "index.html"],
        cwd=REPO_ROOT, check=True,
    )

    commit_msg = f"Add {display_name} ({group})"
    subprocess.run(
        ["git", "commit", "-m", commit_msg],
        cwd=REPO_ROOT, check=True,
    )
    subprocess.run(
        ["git", "push", "-u", "origin", branch],
        cwd=REPO_ROOT, check=True,
    )

    pr_body = (
        f"Adds **{display_name}** to the showdown.\n\n"
        f"- OpenRouter model: `{model_id}`\n"
        f"- Group: {group}\n"
        f"- Generated by Pi coding agent via OpenRouter\n\n"
        f"---\n"
        f"*Automated by the model discovery workflow.*"
    )

    result = subprocess.run(
        ["gh", "pr", "create",
         "--title", f"Add {display_name} ({group})",
         "--body", pr_body,
         "--base", "main",
         "--head", branch],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )

    if result.returncode == 0:
        logging.info(f"PR created: {result.stdout.strip()}")
        return True
    else:
        logging.error(f"Failed to create PR: {result.stderr}")
        return False


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    with open(GROUPS_FILE) as f:
        provider_groups = json.load(f)

    logging.info("Fetching models from OpenRouter API...")
    resp = requests.get(OPENROUTER_API, timeout=30)
    resp.raise_for_status()
    all_models = resp.json()["data"]
    logging.info(f"Total models from API: {len(all_models)}")

    cost_ceiling = get_cost_ceiling(all_models)

    candidates = [m for m in all_models if should_include(m, cost_ceiling)]
    logging.info(f"After filtering: {len(candidates)} candidates")

    if MODEL_FILTER:
        candidates = [m for m in candidates if m["id"] == MODEL_FILTER]
        logging.info(f"Filtered to specific model: {len(candidates)} match")
    else:
        cutoff = time.time() - LOOKBACK_SECONDS
        candidates = [m for m in candidates if m.get("created", 0) >= cutoff]
        logging.info(f"After 7-day recency filter: {len(candidates)} candidates")

    existing_ids = get_existing_openrouter_ids()
    logging.info(f"Existing openrouterIds in index.html: {len(existing_ids)}")

    new_models = []
    for model in candidates:
        provider = model["id"].split("/")[0]
        group = provider_groups.get(provider)
        if group is None:
            logging.info(
                f"Skipping {model['id']} - provider '{provider}' not in provider_groups.json"
            )
            continue

        if model["id"] in existing_ids:
            continue

        slug = generate_slug(model["id"])
        if (REPO_ROOT / slug / "index.html").exists():
            logging.info(f"Skipping {model['id']} - directory {slug}/ already exists")
            continue

        if has_open_pr(slug):
            logging.info(f"Skipping {model['id']} - open PR exists for {slug}")
            continue

        display_name = generate_display_name(model)
        pricing = model.get("pricing", {})
        prompt_price = float(pricing.get("prompt") or "0") * 1_000_000
        completion_price = float(pricing.get("completion") or "0") * 1_000_000

        new_models.append({
            "model": model,
            "slug": slug,
            "display_name": display_name,
            "group": group,
        })
        logging.info(
            f"  NEW: {model['id']} -> slug={slug}, name={display_name}, "
            f"group={group}, ${prompt_price:.2f}/${completion_price:.2f} per 1M tokens"
        )

    logging.info(f"New models to process: {len(new_models)}")

    if DRY_RUN:
        logging.info("Dry run mode - stopping here")
        return

    if not new_models:
        logging.info("No new models found")
        return

    processed = 0
    for nm in new_models[:MAX_MODELS]:
        model_id = nm["model"]["id"]
        slug = nm["slug"]
        display_name = nm["display_name"]
        group = nm["group"]

        logging.info(f"--- Processing {model_id} ---")

        subprocess.run(["git", "checkout", "main"], cwd=REPO_ROOT, check=True)
        subprocess.run(["git", "checkout", "--", "."], cwd=REPO_ROOT)

        with tempfile.TemporaryDirectory(prefix=f"pi-{slug}-") as tmp:
            if not run_pi(model_id, slug, Path(tmp)):
                logging.error(f"Skipping {slug} - Pi generation failed")
                continue

        update_models_array(slug, display_name, group, model_id)

        if create_pr(slug, display_name, group, model_id):
            processed += 1

        subprocess.run(["git", "checkout", "main"], cwd=REPO_ROOT, check=True)
        subprocess.run(["git", "checkout", "--", "."], cwd=REPO_ROOT)

    logging.info(f"Done. Processed {processed}/{len(new_models)} new models.")


if __name__ == "__main__":
    main()

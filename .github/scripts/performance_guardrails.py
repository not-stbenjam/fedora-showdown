#!/usr/bin/env python3
"""Conservative static checks for model pages that may freeze a browser."""

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


PARTICLE_COLLECTIONS = ("particles", "parts", "projected", "visible", "draw")


@dataclass(frozen=True)
class PerformanceAudit:
    reasons: tuple[str, ...]

    @property
    def requires_warning(self):
        return bool(self.reasons)


def _mask_literals_and_comments(source):
    """Replace JS strings and comments with spaces while preserving offsets."""
    result = list(source)
    i = 0
    state = "code"
    quote = ""

    while i < len(source):
        char = source[i]
        next_char = source[i + 1] if i + 1 < len(source) else ""

        if state == "code":
            if char in ("'", '"', "`"):
                state = "string"
                quote = char
                result[i] = " "
            elif char == "/" and next_char == "/":
                state = "line-comment"
                result[i] = result[i + 1] = " "
                i += 1
            elif char == "/" and next_char == "*":
                state = "block-comment"
                result[i] = result[i + 1] = " "
                i += 1
        elif state == "string":
            if char == "\\":
                result[i] = " "
                if i + 1 < len(source):
                    result[i + 1] = " "
                    i += 1
            else:
                if char == quote:
                    state = "code"
                if char != "\n":
                    result[i] = " "
        elif state == "line-comment":
            if char == "\n":
                state = "code"
            else:
                result[i] = " "
        elif state == "block-comment":
            if char == "*" and next_char == "/":
                result[i] = result[i + 1] = " "
                state = "code"
                i += 1
            elif char != "\n":
                result[i] = " "

        i += 1

    return "".join(result)


def _matching_delimiter(source, start, opening, closing):
    depth = 0
    for index in range(start, len(source)):
        if source[index] == opening:
            depth += 1
        elif source[index] == closing:
            depth -= 1
            if depth == 0:
                return index
    return -1


def _loop_blocks(source):
    masked = _mask_literals_and_comments(source)
    for match in re.finditer(r"\bfor\s*\(", masked):
        paren_start = masked.find("(", match.start())
        paren_end = _matching_delimiter(masked, paren_start, "(", ")")
        if paren_end < 0:
            continue

        brace_start = paren_end + 1
        while brace_start < len(masked) and masked[brace_start].isspace():
            brace_start += 1
        if brace_start >= len(masked) or masked[brace_start] != "{":
            continue

        brace_end = _matching_delimiter(masked, brace_start, "{", "}")
        if brace_end < 0:
            continue

        yield (
            masked[paren_start + 1 : paren_end],
            masked[brace_start + 1 : brace_end],
        )


def _brace_depth_at(source, position):
    return source[:position].count("{") - source[:position].count("}")


def audit_html(content):
    reasons = []
    masked = _mask_literals_and_comments(content)
    loops = list(_loop_blocks(content))
    large_budget_names = set()
    for match in re.finditer(
        r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*([^;\n]+)",
        masked,
    ):
        values = [int(value) for value in re.findall(r"\b\d{3,6}\b", match.group(2))]
        if max(values, default=0) >= 5000:
            large_budget_names.add(match.group(1))
    has_large_loop_budget = any(
        re.search(rf"\b{re.escape(name)}\b", header)
        for name in large_budget_names
        for header, _ in loops
    )
    has_dense_literal_particle_budget = any(
        max(
            (int(value) for value in re.findall(r"\b\d{3,6}\b", header)),
            default=0,
        )
        >= 2500
        and re.search(r"\b(?:particles|parts)\s*\.\s*push\s*\(", body)
        for header, body in loops
    )
    has_screen_sampled_particles = (
        bool(re.search(r"\bgetImageData\s*\(", masked))
        and bool(re.search(r"\b(?:particles|parts)\s*\.\s*push\s*\(", masked))
        and any(re.search(r"<\s*H\b", header) for header, _ in loops)
        and any(re.search(r"<\s*W\b", header) for header, _ in loops)
    )
    has_loop_shadow = any(re.search(r"\bshadowBlur\s*=", body) for _, body in loops)
    if (
        has_large_loop_budget
        and has_loop_shadow
        and re.search(r"\brequestAnimationFrame\s*\(", masked)
        and re.search(r"\.sort\s*\(", masked)
    ):
        reasons.append("large sorted animation with canvas shadows")

    for header, body in loops:
        if not any(re.search(rf"\b{name}\b", header) for name in PARTICLE_COLLECTIONS):
            continue
        shadow_matches = list(re.finditer(r"\bshadowBlur\s*=", body))
        has_shadow = bool(shadow_matches)
        has_unconditional_shadow = any(
            _brace_depth_at(body, match.start()) == 0 for match in shadow_matches
        )
        has_gradient = bool(re.search(r"\bcreate(?:Radial|Linear)Gradient\s*\(", body))
        has_multiple_particle_draws = bool(
            re.search(r"\bfillRect\s*\(", body) and re.search(r"\barc\s*\(", body)
        )

        if has_shadow and (
            has_large_loop_budget
            or (
                has_unconditional_shadow
                and (
                    has_dense_literal_particle_budget
                    or has_screen_sampled_particles
                    or has_multiple_particle_draws
                )
            )
        ):
            reasons.append("per-particle canvas shadows")
        if has_gradient and has_large_loop_budget:
            reasons.append("per-particle canvas gradients")

    return PerformanceAudit(tuple(dict.fromkeys(reasons)))


def warning_message(audit):
    if not audit.requires_warning:
        return ""
    return (
        f"This output uses {', '.join(audit.reasons)} in its animation loop. "
        "It may freeze or crash the browser tab."
    )


def _has_preload_warning(index_html, slug):
    id_match = re.search(rf'\bid\s*:\s*"{re.escape(slug)}"', index_html)
    if not id_match:
        return False
    entry_start = index_html.rfind("{", 0, id_match.start())
    if entry_start < 0:
        return False
    masked = _mask_literals_and_comments(index_html)
    entry_end = _matching_delimiter(masked, entry_start, "{", "}")
    if entry_end < 0:
        return False
    entry = index_html[entry_start : entry_end + 1]
    return bool(
        re.search(
            r"\bperfWarning\s*:\s*(?:true|\"[^\"]+\"|\{)",
            entry,
        )
    )


def find_unguarded_models(model_paths, index_html):
    violations = []
    for path in model_paths:
        path = Path(path)
        audit = audit_html(path.read_text(errors="replace"))
        if audit.requires_warning and not _has_preload_warning(index_html, path.parent.name):
            violations.append(
                f"{path.parent.name}: {', '.join(audit.reasons)}; "
                "add perfWarning before this page can load automatically"
            )
    return violations


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("model_pages", nargs="+", type=Path)
    parser.add_argument("--index", type=Path, default=Path("index.html"))
    args = parser.parse_args()

    violations = find_unguarded_models(
        args.model_pages,
        args.index.read_text(errors="replace"),
    )
    if violations:
        for violation in violations:
            print(f"ERROR: {violation}")
        raise SystemExit(1)

    print(f"Performance guardrails passed for {len(args.model_pages)} model page(s).")


if __name__ == "__main__":
    main()

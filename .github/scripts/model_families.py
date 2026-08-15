#!/usr/bin/env python3
"""Pure helpers for grouping model variants in the FedoraBench sidebar."""

from __future__ import annotations

from typing import Iterable


VARIANT_PRIORITY = {
    "Ultracode": 100,
    "Ultra": 90,
    "Max": 80,
    "XHigh": 70,
    "High": 60,
    "Medium": 50,
    "Low": 40,
    "Minimal": 30,
    "None": 20,
    "Off": 20,
    "Default": 10,
}


def family_id(model: dict) -> str | None:
    """Return an explicit family id, or None for ungrouped models."""
    value = model.get("family")
    if not value:
        return None
    return str(value)


def family_members(models: list[dict], family: str) -> list[int]:
    """Return MODELS indices belonging to a family, preserving order."""
    return [index for index, model in enumerate(models) if family_id(model) == family]


def should_group_family(models: list[dict], family: str | None) -> bool:
    """Families are disclosed only when they contain two or more members."""
    if not family:
        return False
    return len(family_members(models, family)) >= 2


def variant_priority(model: dict) -> int:
    """Return the sidebar rank for a family variant, highest effort first."""
    return VARIANT_PRIORITY.get(str(model.get("badge") or "Default"), 0)


def group_section_indices(models: list[dict], indices: Iterable[int]) -> list[dict]:
    """
    Collapse same-family indices into disclosure groups for one sidebar section.

    Returns a list of entries:
      {"type": "model", "index": int}
      {"type": "family", "family": str, "indices": list[int]}
    Singleton or missing families stay as plain model entries.
    """
    ordered = list(indices)
    emitted: set[int] = set()
    entries: list[dict] = []

    for index in ordered:
        if index in emitted:
            continue
        model = models[index]
        family = family_id(model)
        members = sorted(
            (member for member in ordered if family_id(models[member]) == family),
            key=lambda member: variant_priority(models[member]),
            reverse=True,
        )
        if family and len(members) >= 2:
            entries.append({"type": "family", "family": family, "indices": members})
            emitted.update(members)
        else:
            entries.append({"type": "model", "index": index})
            emitted.add(index)

    return entries


def family_matches_query(models: list[dict], indices: list[int], query: str) -> list[int]:
    """Return family member indices that match a sidebar search query."""
    q = query.strip().lower()
    if not q:
        return list(indices)
    matched = []
    for index in indices:
        model = models[index]
        haystack = " ".join(
            str(part)
            for part in (
                model.get("name"),
                model.get("group"),
                model.get("badge"),
                model.get("id"),
                model.get("family"),
            )
            if part
        ).lower()
        if q in haystack:
            matched.append(index)
    return matched


def flatten_nav_order(section_entries: list[dict]) -> list[int]:
    """Sidebar prev/next walks every model button, including collapsed variants."""
    order: list[int] = []
    for entry in section_entries:
        if entry["type"] == "model":
            order.append(entry["index"])
        else:
            order.extend(entry["indices"])
    return order

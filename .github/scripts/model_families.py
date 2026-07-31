#!/usr/bin/env python3
"""Pure helpers for grouping model variants in the FedoraBench sidebar."""

from __future__ import annotations

from typing import Iterable


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
        if should_group_family(models, family):
            members = [member for member in ordered if family_id(models[member]) == family]
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

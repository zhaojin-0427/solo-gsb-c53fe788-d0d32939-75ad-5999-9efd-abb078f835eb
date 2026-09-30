"""Semantic validation beyond what Pydantic can express declaratively.

All problems here are reported with HTTP 422 (the request is structurally
valid JSON but does not describe a well-formed tolerance problem).
"""

from collections import Counter

from fastapi import HTTPException

from .models import PlanRequest


def validate_request(req: PlanRequest) -> None:
    """Raise HTTPException(422) on duplicate ids, dangling references, etc."""
    errors: list[str] = []

    # --- batch groups -------------------------------------------------------
    group_ids = [g.id for g in req.batch_groups]
    dup_groups = {gid for gid, n in Counter(group_ids).items() if n > 1}
    if dup_groups:
        errors.append(f"duplicate group id(s): {sorted(dup_groups)}")
    known_groups = set(group_ids)

    # --- parts --------------------------------------------------------------
    part_ids = [p.id for p in req.parts]
    dup_parts = {pid for pid, n in Counter(part_ids).items() if n > 1}
    if dup_parts:
        errors.append(f"duplicate part id(s): {sorted(dup_parts)}")
    known_parts = set(part_ids)

    for part in req.parts:
        if part.batch_group_id not in known_groups:
            errors.append(
                f"part '{part.id}' references unknown group "
                f"'{part.batch_group_id}'"
            )
        # Every part must offer at least one selectable process档.
        # min_length=1 handles [], this handles the semantic surface.
        if not part.options:
            errors.append(f"part '{part.id}' has no process options")
        opt_ids = [o.id for o in part.options]
        dup_opts = {oid for oid, n in Counter(opt_ids).items() if n > 1}
        if dup_opts:
            errors.append(
                f"part '{part.id}' has duplicate option id(s): {sorted(dup_opts)}"
            )

    # --- gaps ---------------------------------------------------------------
    gap_ids = [g.id for g in req.gaps]
    dup_gaps = {gid for gid, n in Counter(gap_ids).items() if n > 1}
    if dup_gaps:
        errors.append(f"duplicate gap id(s): {sorted(dup_gaps)}")

    for gap in req.gaps:
        if gap.lower > gap.upper:
            errors.append(
                f"gap '{gap.id}' has inverted interval "
                f"[{gap.lower}, {gap.upper}]"
            )
        unknown_refs = sorted(set(gap.coefficients) - known_parts)
        if unknown_refs:
            errors.append(
                f"gap '{gap.id}' references unknown part id(s): {unknown_refs}"
            )

    if errors:
        raise HTTPException(
            status_code=422,
            detail={"message": "invalid tolerance plan request", "errors": errors},
        )

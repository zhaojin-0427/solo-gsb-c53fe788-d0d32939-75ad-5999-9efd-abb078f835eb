"""请求语义校验：重复 ID、缺失引用、倒置区间等一律返回 422。"""
from __future__ import annotations

from fastapi import HTTPException

from .schemas import INT_LIMIT, PlanRequest


def _error(message: str) -> HTTPException:
    # 与 FastAPI 请求体校验错误保持同一状态码 422。
    return HTTPException(status_code=422, detail=message)


def validate_request(req: PlanRequest) -> None:
    # ---- 批组 ID 唯一 ----
    group_ids: set[str] = set()
    for g in req.batch_groups:
        if g.id in group_ids:
            raise _error(f"批组 ID 重复: {g.id}")
        group_ids.add(g.id)

    # ---- 零件 ID 唯一、批组引用存在；零件内工艺档 ID 唯一 ----
    part_ids: set[str] = set()
    for p in req.parts:
        if p.id in part_ids:
            raise _error(f"零件 ID 重复: {p.id}")
        part_ids.add(p.id)
        if p.batch_group_id not in group_ids:
            raise _error(
                f"零件 {p.id} 引用了不存在的批组: {p.batch_group_id}"
            )
        option_ids: set[str] = set()
        for o in p.options:
            if o.id in option_ids:
                raise _error(f"零件 {p.id} 下工艺档 ID 重复: {o.id}")
            option_ids.add(o.id)

    # ---- 间隙 ID 唯一、区间非倒置、系数引用存在且同零件只允许一条 ----
    gap_ids: set[str] = set()
    for gap in req.gaps:
        if gap.id in gap_ids:
            raise _error(f"间隙 ID 重复: {gap.id}")
        gap_ids.add(gap.id)
        if gap.allowed_low > gap.allowed_high:
            raise _error(
                f"间隙 {gap.id} 的允许区间倒置: "
                f"{gap.allowed_low} > {gap.allowed_high}"
            )
        referenced: set[str] = set()
        for c in gap.coefficients:
            if c.part_id not in part_ids:
                raise _error(
                    f"间隙 {gap.id} 引用了不存在的零件: {c.part_id}"
                )
            if c.part_id in referenced:
                raise _error(
                    f"间隙 {gap.id} 中零件 {c.part_id} 的系数重复给出"
                )
            referenced.add(c.part_id)

    _check_overflow(req)


def _check_overflow(req: PlanRequest) -> None:
    """CP-SAT 为 int64 求解器；显式保证任何中间量与域界都不溢出。"""
    group_by_id = {g.id: g for g in req.batch_groups}
    part_by_id = {p.id: p for p in req.parts}

    # 零件实际尺寸可达范围。
    for p in req.parts:
        lim = group_by_id[p.batch_group_id].shift_limit
        rmax = max(o.residual_limit for o in p.options)
        if abs(p.nominal_size) + lim + rmax > INT_LIMIT:
            raise _error(
                f"零件 {p.id} 的实际尺寸范围超出可表示整数上限"
            )

    # 每个间隙的名义中心与最坏偏差都不得越界（中心在选档前即为定值）。
    for gap in req.gaps:
        center = gap.constant
        half = 0
        for c in gap.coefficients:
            p = part_by_id[c.part_id]
            lim = group_by_id[p.batch_group_id].shift_limit
            rmax = max(o.residual_limit for o in p.options)
            center += c.value * p.nominal_size
            half += abs(c.value) * (lim + rmax)
        if abs(center) + half > INT_LIMIT:
            raise _error(f"间隙 {gap.id} 的取值范围超出可表示整数上限")

    total_cost = sum(min(o.cost for o in p.options) for p in req.parts)
    if total_cost > INT_LIMIT:
        raise _error("成本之和超出可表示整数上限")

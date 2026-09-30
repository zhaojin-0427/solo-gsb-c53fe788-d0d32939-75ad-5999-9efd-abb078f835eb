"""最坏情况公差分配求解器（OR-Tools CP-SAT）。

模型
----
每件零件 i 选一个工艺档 o；实际尺寸
    actual_i = nominal_i + s_{group(i)} + r_i，
其中 s_g ∈ [-L_g, L_g] 为批组共同偏移（同一批组共享），
r_i ∈ [-R_{i,o}, R_{i,o}] 为该档独立残差。

间隙 g：
    gap_g = constant_g + Σ_i c_{g,i} * actual_i。
在所有扰动下合格，当且仅当对每个 g：
    allowed_low  ≤ center_g - slack_g(x) 且
    center_g + slack_g(x) ≤ allowed_high，
其中
    center_g = constant + Σ c * nominal（与选档无关），
    slack_g(x) = Σ_g |A_{g,g'}| L_{g'} + Σ_i |c_{g,i}| R_{i,x_i}。
批组共同项在任何方案下都固定，只有残差项随选档变化。

目标：总成本最小；成本并列时，按零件 ID 排序后的工艺档 ID 向量
字典序最小（锁定成本后逐位置最小化工艺档 ID 升序序号实现）。
"""
from __future__ import annotations

from dataclasses import dataclass

from ortools.sat.python import cp_model

from .schemas import PlanRequest

# 每轮 CP-SAT 求解的时间上限（秒）；规模为装配方案，默认秒级可解。
SOLVER_TIME_LIMIT_SECONDS = 60


class InfeasibleError(Exception):
    """不存在任何在全部扰动下合格的方案（对应 HTTP 409）。"""


@dataclass(frozen=True)
class GapContext:
    gap_id: str
    constant: int
    allowed_low: int
    allowed_high: int
    nominal_center: int      # 不含任何扰动
    group_half: int          # 批组共同偏移贡献的最坏偏差（固定）
    # part_index -> 系数；只包含非零系数
    coeffs: dict[int, int]


def _build_gap_contexts(req: PlanRequest) -> list[GapContext]:
    group_index = {g.id: i for i, g in enumerate(req.batch_groups)}
    part_index = {p.id: i for i, p in enumerate(req.parts)}
    n_groups = len(req.batch_groups)

    contexts: list[GapContext] = []
    for gap in req.gaps:
        center = gap.constant
        agg = [0] * n_groups
        part_coeffs: dict[int, int] = {}
        for c in gap.coefficients:
            pi = part_index[c.part_id]
            p = req.parts[pi]
            center += c.value * p.nominal_size
            agg[group_index[p.batch_group_id]] += c.value
            if c.value != 0:
                part_coeffs[pi] = c.value

        group_half = 0
        for gi, agg_value in enumerate(agg):
            if agg_value != 0:
                group_half += abs(agg_value) * req.batch_groups[gi].shift_limit
        contexts.append(
            GapContext(
                gap_id=gap.id,
                constant=gap.constant,
                allowed_low=gap.allowed_low,
                allowed_high=gap.allowed_high,
                nominal_center=center,
                group_half=group_half,
                coeffs=part_coeffs,
            )
        )
    return contexts


def solve(req: PlanRequest) -> dict:
    """求解并返回方案结构；无解抛 InfeasibleError。"""
    # 零件按 ID 排序：决策变量顺序即字典序所用的向量顺序。
    part_order = sorted(range(len(req.parts)), key=lambda i: req.parts[i].id)

    # 每零件内部工艺档也按 ID 排序，方便 witness 与字典序处理。
    option_order = [
        sorted(range(len(p.options)), key=lambda j: p.options[j].id)
        for p in req.parts
    ]

    contexts = _build_gap_contexts(req)

    # ---- 构造基础 CP-SAT 模型 ----
    model = cp_model.CpModel()
    b: list[list[cp_model.IntVar]] = []  # b[i][j]: 零件 i 选其第 j 档
    for i in part_order:
        p = req.parts[i]
        vars_i = [
            model.new_bool_var(f"sel_p{p.id}_o{p.options[j].id}")
            for j in option_order[i]
        ]
        model.add_exactly_one(vars_i)
        b.append(vars_i)

    # selected_residual[i] = 所选档的残差上限。
    selected_residual: list[cp_model.IntVar] = []
    selected_cost: list[cp_model.IntVar] = []
    for pos, i in enumerate(part_order):
        p = req.parts[i]
        r = model.new_int_var(0, max(o.residual_limit for o in p.options),
                              f"res_p{p.id}")
        model.add(
            r == sum(
                b[pos][k] * p.options[j].residual_limit
                for k, j in enumerate(option_order[i])
            )
        )
        selected_residual.append(r)

        c = model.new_int_var(0, max(o.cost for o in p.options),
                              f"cost_p{p.id}")
        model.add(
            c == sum(
                b[pos][k] * p.options[j].cost
                for k, j in enumerate(option_order[i])
            )
        )
        selected_cost.append(c)

    # 总成本上界由 validate_request 的整数上限检查保证不越界。
    total_cost = model.new_int_var(0, (1 << 62) - 1, "total_cost")
    model.add(total_cost == sum(selected_cost))

    # rank[pos]：当前位置所选工艺档在“按 ID 升序”序列中的序号。
    rank: list[cp_model.IntVar] = []
    for pos, i in enumerate(part_order):
        p = req.parts[i]
        k_var = model.new_int_var(
            0, len(option_order[i]) - 1, f"rank_p{p.id}"
        )
        model.add(
            k_var
            == sum(k * b[pos][k] for k in range(len(option_order[i])))
        )
        rank.append(k_var)

    # ---- 间隙最坏情况约束 ----
    for ctx in contexts:
        residual_half = sum(
            abs(ctx.coeffs[i]) * selected_residual[pos]
            for pos, i in enumerate(part_order)
            if i in ctx.coeffs
        )
        slack = ctx.group_half + residual_half
        model.add(ctx.nominal_center - slack >= ctx.allowed_low)
        model.add(ctx.nominal_center + slack <= ctx.allowed_high)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = SOLVER_TIME_LIMIT_SECONDS

    def _require_optimal(status) -> None:
        if status == cp_model.INFEASIBLE:
            raise InfeasibleError(
                "任何工艺档组合都无法在全部扰动下满足间隙要求"
            )
        # 限时得到可行但未证明最优 / 完全无解时间耗尽：不能保证给出的是
        # 全局最低成本方案，按服务端错误处理而非静默返回近似解。
        raise RuntimeError(f"CP-SAT 未能在时限内证明最优，状态: {solver.status_name(status)}")

    # ---- 第一步：最小化总成本 ----
    model.minimize(total_cost)
    status = solver.solve(model)
    if status != cp_model.OPTIMAL:
        _require_optimal(status)
    best_cost = solver.value(total_cost)
    # 锁定最优成本，后续只在并列方案中选择。
    model.add(total_cost == best_cost)

    # ---- 第二步：成本并列下字典序最小化工艺档 ID 向量 ----
    # 对按零件 ID 排序后的位置从前到后，每轮最小化该位置工艺档的 ID
    # 序号（等价于最小化 ID 本身），随后固定该选择。
    chosen: list[int] = []
    for pos, i in enumerate(part_order):
        model.minimize(rank[pos])
        st = solver.solve(model)
        if st != cp_model.OPTIMAL:
            _require_optimal(st)
        k_star = solver.value(rank[pos])
        chosen.append(option_order[i][k_star])
        model.add(b[pos][k_star] == 1)

    return _format_result(req, part_order, chosen, best_cost, contexts)


def _format_result(
    req: PlanRequest,
    part_order: list[int],
    chosen: list[int],
    best_cost: int,
    contexts: list[GapContext],
) -> dict:
    chosen_by_part: dict[int, int] = {
        i: chosen[pos] for pos, i in enumerate(part_order)
    }

    selections = [
        {"part_id": req.parts[i].id,
         "option_id": req.parts[i].options[chosen_by_part[i]].id}
        for i in part_order
    ]

    group_index = {g.id: i for i, g in enumerate(req.batch_groups)}

    gap_results = []
    for ctx in contexts:
        residual_half = sum(
            abs(ctx.coeffs[i])
            * req.parts[i].options[chosen_by_part[i]].residual_limit
            for i in ctx.coeffs
        )
        half = ctx.group_half + residual_half
        min_value = ctx.nominal_center - half
        max_value = ctx.nominal_center + half

        # 极值见证：按极值方向给出每个批组偏移、每件残差。
        # 批组项方向 = -sign(Σ零件系数)（最小间隙时）；残差方向 = -sign(c)。
        n_groups = len(req.batch_groups)
        agg = [0] * n_groups
        for i in ctx.coeffs:
            gi = group_index[req.parts[i].batch_group_id]
            agg[gi] += ctx.coeffs[i]

        for extreme in ("min", "max"):
            sign = -1 if extreme == "min" else 1
            shifts = {}
            for g in req.batch_groups:
                gi = group_index[g.id]
                a = agg[gi]
                if a == 0 or g.shift_limit == 0:
                    shifts[g.id] = 0
                else:
                    shifts[g.id] = sign * (1 if a > 0 else -1) * g.shift_limit
            residuals = {}
            for i, p in enumerate(req.parts):
                c = ctx.coeffs.get(i, 0)
                r_lim = p.options[chosen_by_part[i]].residual_limit
                if c == 0 or r_lim == 0:
                    residuals[p.id] = 0
                else:
                    residuals[p.id] = sign * (1 if c > 0 else -1) * r_lim
            if extreme == "min":
                min_witness = {
                    "batch_group_shifts": shifts,
                    "part_residuals": residuals,
                }
            else:
                max_witness = {
                    "batch_group_shifts": shifts,
                    "part_residuals": residuals,
                }

        gap_results.append({
            "gap_id": ctx.gap_id,
            "allowed_low": ctx.allowed_low,
            "allowed_high": ctx.allowed_high,
            "nominal_center": ctx.nominal_center,
            "min_value": min_value,
            "max_value": max_value,
            "min_witness": min_witness,
            "max_witness": max_witness,
        })

    return {
        "selections": selections,
        "total_cost": best_cost,
        "gaps": gap_results,
    }

"""Worst-case clearance feasibility solver built on OR-Tools CP-SAT.

Mathematical model (all sizes are integer micrometres)
------------------------------------------------------
For part p choose exactly one process档 o:
    actual_size_p = nominal_p + group_offset[g(p)] + residual_p
with group offsets in [-B_g, B_g] and residuals in [-r_o, r_o] varying
adversarially and independently.

For gap j:  G_j = c_j + sum_p a_{jp} * actual_size_p.

Extrema over all perturbations:
    G_j^min = nominal_j - sum_g |A_{jg}| B_g - sum_p |a_{jp}| r_{o_p}
    G_j^max = nominal_j + sum_g |A_{jg}| B_g + sum_p |a_{jp}| r_{o_p}
where A_{jg} = sum of a_{jp} over parts p of group g and
      nominal_j = c_j + sum_p a_{jp} * nominal_p.

A choice is robustly feasible iff G_j^min >= lower_j and G_j^max <= upper_j
for every gap.  Both inequalities are linear in the chosen residual bounds,
which makes the problem a finite-domain integer program solved by CP-SAT.

Cost ties are broken by the lexicographically smallest vector of option ids
with parts ordered by part id (plain string / Unicode code-point order).
"""

from ortools.sat.python import cp_model

from .models import (
    BatchGroup,
    Gap,
    GapExtreme,
    Part,
    PlanRequest,
    PlanResponse,
    SelectedOption,
    Witness,
)

SOLVER_TIME_LIMIT_SECONDS = 30.0
NUM_WORKERS = 8


class InfeasiblePlanError(Exception):
    """Raised when no selection keeps every gap inside its interval."""


class SolverFailureError(Exception):
    """Raised when the solver cannot prove a result within its limits."""


# ---------------------------------------------------------------------------
# Static preprocessing
# ---------------------------------------------------------------------------


class _GapData:
    __slots__ = (
        "gap",
        "nominal",
        "abs_part_coef",
        "half_width_const",
        "tolerance",
    )

    def __init__(self, gap: Gap, parts: list[Part], groups: list[BatchGroup]):
        self.gap = gap
        coefs = gap.coefficients

        nominal = gap.constant
        group_sum: dict[str, int] = {}
        for p in parts:
            a = coefs.get(p.id, 0)
            if a:
                nominal += a * p.nominal_size
                group_sum[p.batch_group_id] = (
                    group_sum.get(p.batch_group_id, 0) + a
                )
        self.nominal = nominal
        self.abs_part_coef = {p.id: abs(coefs.get(p.id, 0)) for p in parts}

        group_bound = {g.id: g.offset_bound for g in groups}
        self.half_width_const = sum(
            abs(s) * group_bound[gid] for gid, s in group_sum.items()
        )

        # Slack of the symmetric clearance interval around the nominal value:
        # lower <= nominal +/- (const_width + variable_width) <= upper
        self.tolerance = (
            min(gap.upper - nominal, nominal - gap.lower) - self.half_width_const
        )


# ---------------------------------------------------------------------------
# CP-SAT model
# ---------------------------------------------------------------------------


def _element(model, index_var, table: list[int], name: str):
    """Return an IntVar equal to table[index_var]."""
    value = model.NewIntVar(min(table), max(table), name)
    model.AddElement(index_var, table, value)
    return value


def _build_model(
    parts: list[Part],
    gap_data: list[_GapData],
    fixed_options: dict[int, int],
    pinned_cost: int | None,
):
    """Build a CP-SAT model.

    fixed_options pins part index -> option index for lexicographic stages.
    pinned_cost pins total cost to the proven optimum during tie breaking;
    when None the objective is total cost itself.
    Returns (model, total_cost_expr, rank_vars).
    """
    model = cp_model.CpModel()
    rank_vars = []
    cost_terms = []
    width_terms: list[list[cp_model.LinearExpr]] = [[] for _ in gap_data]

    for pi, part in enumerate(parts):
        n = len(part.options)
        r = model.NewIntVar(0, n - 1, f"rank_{part.id}")
        rank_vars.append(r)
        if pi in fixed_options:
            model.Add(r == fixed_options[pi])

        picks = [model.NewBoolVar(f"x_{part.id}_{k}") for k in range(n)]
        model.AddExactlyOne(picks)
        model.Add(r == sum(k * picks[k] for k in range(n)))

        costs = [o.cost for o in part.options]
        bounds = [o.residual_bound for o in part.options]
        cost_terms.append(_element(model, r, costs, f"cost_{part.id}"))
        chosen_bound = _element(model, r, bounds, f"bound_{part.id}")

        for gj, gd in enumerate(gap_data):
            w = gd.abs_part_coef.get(part.id, 0)
            if w:
                width_terms[gj].append(w * chosen_bound)

    total_cost = sum(cost_terms)
    for gd, terms in zip(gap_data, width_terms):
        # Variable residual spread must stay within the remaining slack.
        model.Add(sum(terms) <= gd.tolerance)

    if pinned_cost is None:
        model.minimize(total_cost)
    else:
        model.Add(total_cost == pinned_cost)
        # Prefix parts are pinned through fixed_options; minimize the rank of
        # the next part to obtain the lexicographically smallest option-id
        # vector with parts ordered by part id.  Stages run one per part.
        model.minimize(rank_vars[len(fixed_options)])

    return model, total_cost, rank_vars


def _solve(model: cp_model.CpModel) -> tuple[int, cp_model.CpSolver]:
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = SOLVER_TIME_LIMIT_SECONDS
    solver.parameters.num_search_workers = NUM_WORKERS
    solver.parameters.log_search_progress = False
    status = solver.Solve(model)
    if status in (cp_model.OPTIMAL, cp_model.INFEASIBLE):
        return status, solver
    raise SolverFailureError(f"CP-SAT ended with status {solver.StatusName(status)}")


# ---------------------------------------------------------------------------
# Extremes and perturbation witnesses
# ---------------------------------------------------------------------------


def _sign(x: int) -> int:
    return 1 if x >= 0 else -1


def _gap_extremes_with_witnesses(
    req: PlanRequest,
    chosen_bounds: dict[str, int],
    group_order: list[str],
) -> tuple[list[GapExtreme], list[Witness]]:
    parts = req.parts
    parts_sorted = sorted(parts, key=lambda p: p.id)
    group_bound = {g.id: g.offset_bound for g in req.batch_groups}
    part_group = {p.id: p.batch_group_id for p in parts}

    extremes: list[GapExtreme] = []
    witnesses: list[Witness] = []

    for gap in req.gaps:
        coefs = gap.coefficients

        group_coef_sum: dict[str, int] = {}
        for p in parts:
            a = coefs.get(p.id, 0)
            if a:
                gid = p.batch_group_id
                group_coef_sum[gid] = group_coef_sum.get(gid, 0) + a

        nominal = gap.constant + sum(
            coefs.get(p.id, 0) * p.nominal_size for p in parts
        )
        half_width = sum(
            abs(group_coef_sum.get(gid, 0)) * group_bound[gid]
            for gid in group_order
        ) + sum(abs(coefs.get(p.id, 0)) * chosen_bounds[p.id] for p in parts)

        minimum = nominal - half_width
        maximum = nominal + half_width
        extremes.append(
            GapExtreme(gap_id=gap.id, minimum=minimum, maximum=maximum)
        )

        # Perturbation witness: pick every disturbance with the sign that
        # drives the gap towards the requested extreme.  A zero coefficient
        # conventionally gets the + endpoint.
        for extreme, sign in (("minimum", -1), ("maximum", 1)):
            offsets = {
                gid: sign * _sign(group_coef_sum.get(gid, 0)) * group_bound[gid]
                for gid in group_order
            }
            residuals = {
                p.id: sign * _sign(coefs.get(p.id, 0)) * chosen_bounds[p.id]
                for p in parts_sorted
            }
            value = gap.constant + sum(
                coefs.get(p.id, 0)
                * (
                    p.nominal_size
                    + offsets[part_group[p.id]]
                    + residuals[p.id]
                )
                for p in parts
            )
            assert value == (maximum if sign == 1 else minimum), (
                f"witness for gap {gap.id} {extreme} does not attain the extreme"
            )
            witnesses.append(
                Witness(
                    gap_id=gap.id,
                    extreme=extreme,
                    group_offsets=offsets,
                    part_residuals=residuals,
                    value=value,
                )
            )

    return extremes, witnesses


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def solve_plan(req: PlanRequest) -> PlanResponse:
    # Parts ordered by part id and, within each part, options ordered by
    # option id: a CP-SAT rank then equals the position in the lexicographic
    # ordering used by the cost-tie rule.
    parts_sorted = sorted(req.parts, key=lambda p: p.id)
    for part in parts_sorted:
        part.options.sort(key=lambda o: o.id)
    groups = req.batch_groups
    gap_data = [_GapData(g, parts_sorted, groups) for g in req.gaps]

    # With zero residual spread the clearance can still not fit the interval:
    # no selection can reduce the disturbance any further.
    if any(gd.tolerance < 0 for gd in gap_data):
        raise InfeasiblePlanError

    try:
        # Stage 1: minimum cost.
        model, cost_expr, _ = _build_model(parts_sorted, gap_data, {}, None)
        status, solver = _solve(model)
        if status == cp_model.INFEASIBLE:
            raise InfeasiblePlanError
        optimal_cost = solver.Value(cost_expr)

        # Stage 2: lexicographically smallest option-id vector.  Fix the
        # minimum-ranked feasible option for parts in part-id order one at a
        # time.
        fixed: dict[int, int] = {}
        for pi in range(len(parts_sorted)):
            model, _, rank_vars = _build_model(
                parts_sorted, gap_data, fixed, optimal_cost
            )
            status, solver = _solve(model)
            if status == cp_model.INFEASIBLE:
                raise SolverFailureError("tie-breaking stage became infeasible")
            fixed[pi] = solver.Value(rank_vars[pi])
    except InfeasiblePlanError:
        raise
    except SolverFailureError:
        raise
    except Exception as exc:
        raise SolverFailureError(str(exc)) from exc

    selected: list[SelectedOption] = []
    chosen_bounds: dict[str, int] = {}
    for pi, part in enumerate(parts_sorted):
        opt = part.options[fixed[pi]]
        chosen_bounds[part.id] = opt.residual_bound
        selected.append(
            SelectedOption(part_id=part.id, option_id=opt.id, cost=opt.cost)
        )

    group_order = [g.id for g in groups]
    extremes, witnesses = _gap_extremes_with_witnesses(
        req, chosen_bounds, group_order
    )

    return PlanResponse(
        plan=selected,
        total_cost=optimal_cost,
        gap_extremes=extremes,
        witnesses=witnesses,
    )

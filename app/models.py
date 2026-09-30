"""Pydantic schemas for the mechanical assembly tolerance planning service."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

# Every dimensional quantity is an integer number of micrometres.
# StrictInt rejects booleans (bool is a subclass of int in Python).
NonNegativeInt = Annotated[int, StrictInt, Field(ge=0)]
PartCoefficient = StrictInt
GroupOffsetBound = NonNegativeInt
PartId = Annotated[str, Field(min_length=1)]
BatchGroupId = Annotated[str, Field(min_length=1)]
ProcessOptionId = Annotated[str, Field(min_length=1)]
GapId = Annotated[str, Field(min_length=1)]


class ProcessOption(BaseModel):
    """One selectable process档 for a part."""

    model_config = ConfigDict(extra="forbid")

    id: ProcessOptionId
    cost: NonNegativeInt
    residual_bound: NonNegativeInt


class Part(BaseModel):
    """A part with its nominal size, batch group and process档s."""

    model_config = ConfigDict(extra="forbid")

    id: PartId
    nominal_size: StrictInt
    batch_group_id: BatchGroupId
    options: list[ProcessOption] = Field(min_length=1)


class BatchGroup(BaseModel):
    """A set of parts sharing a common offset disturbance."""

    model_config = ConfigDict(extra="forbid")

    id: BatchGroupId
    offset_bound: GroupOffsetBound


class Gap(BaseModel):
    """A linear clearance expression: constant + sum(coef * actual_size)."""

    model_config = ConfigDict(extra="forbid")

    id: GapId
    constant: StrictInt
    # Sparse: parts whose coefficient is zero may be omitted.
    coefficients: dict[PartId, PartCoefficient]
    # Inclusive closed interval [lower, upper] of accepted clearance values.
    lower: StrictInt
    upper: StrictInt


class PlanRequest(BaseModel):
    """Request body of POST /plans."""

    model_config = ConfigDict(extra="forbid")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    parts: list[Part] = Field(min_length=1)
    batch_groups: list[BatchGroup] = Field(alias="groups", min_length=1)
    gaps: list[Gap] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class SelectedOption(BaseModel):
    part_id: PartId
    option_id: ProcessOptionId
    cost: NonNegativeInt


class GapExtreme(BaseModel):
    gap_id: GapId
    minimum: StrictInt
    maximum: StrictInt


class Witness(BaseModel):
    """A perturbation vector attaining a clearance extreme."""

    gap_id: GapId
    extreme: Literal["minimum", "maximum"]
    group_offsets: dict[BatchGroupId, StrictInt]
    part_residuals: dict[PartId, StrictInt]
    value: StrictInt


class PlanResponse(BaseModel):
    plan: list[SelectedOption]
    total_cost: NonNegativeInt
    gap_extremes: list[GapExtreme]
    witnesses: list[Witness]

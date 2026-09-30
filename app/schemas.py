"""请求/响应数据模型。

所有尺寸量均为整数微米；上限与成本均为非负整数。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

# CP-SAT 的整数域为 int64；求解前统一在 2^62 处设防，中间运算也不会溢出 int64。
INT_LIMIT = (1 << 62) - 1


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    @field_validator("id", check_fields=False)
    @classmethod
    def _id_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("ID 不能为空串")
        return v


class BatchGroupIn(StrictModel):
    """批组：组内所有零件共享同一共同偏移。"""

    id: str = Field(min_length=1)
    shift_limit: int = Field(ge=0, le=INT_LIMIT)


class OptionIn(StrictModel):
    """工艺档：成本与该档独立残差的对称上限（微米）。"""

    id: str = Field(min_length=1)
    cost: int = Field(ge=0, le=INT_LIMIT)
    residual_limit: int = Field(ge=0, le=INT_LIMIT)


class PartIn(StrictModel):
    """零件：名义尺寸、所属批组、可选工艺档。"""

    id: str = Field(min_length=1)
    nominal_size: int = Field(ge=-INT_LIMIT, le=INT_LIMIT)
    batch_group_id: str = Field(min_length=1)
    options: list[OptionIn] = Field(min_length=1)


class CoefficientIn(StrictModel):
    """间隙表达式中的一个零件整数系数项。"""

    part_id: str = Field(min_length=1)
    value: int = Field(ge=-INT_LIMIT, le=INT_LIMIT)


class GapIn(StrictModel):
    """装配间隙：constant + Σ value * 实际尺寸，须落在闭区间 [low, high]。"""

    id: str = Field(min_length=1)
    constant: int = Field(ge=-INT_LIMIT, le=INT_LIMIT)
    coefficients: list[CoefficientIn] = Field(default_factory=list)
    allowed_low: int = Field(ge=-INT_LIMIT, le=INT_LIMIT)
    allowed_high: int = Field(ge=-INT_LIMIT, le=INT_LIMIT)


class PlanRequest(StrictModel):
    batch_groups: list[BatchGroupIn] = Field(min_length=1)
    parts: list[PartIn] = Field(min_length=1)
    gaps: list[GapIn] = Field(min_length=1)


class Witness(BaseModel):
    """取到间隙极值时的一组扰动见证。"""

    batch_group_shifts: dict[str, int]
    part_residuals: dict[str, int]


class GapResult(BaseModel):
    gap_id: str
    allowed_low: int
    allowed_high: int
    nominal_center: int
    min_value: int
    max_value: int
    min_witness: Witness
    max_witness: Witness


class Selection(BaseModel):
    part_id: str
    option_id: str


class PlanResponse(BaseModel):
    selections: list[Selection]
    total_cost: int
    gaps: list[GapResult]

"""机械装配公差方案服务。"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from .schemas import PlanRequest, PlanResponse
from .solver import InfeasibleError, solve
from .validation import validate_request

app = FastAPI(
    title="机械装配公差方案服务",
    description=(
        "为每件零件选择一个工艺档，在批组共同偏移与独立残差的任意"
        "扰动下保证全部装配间隙合格，并最小化总成本。"
    ),
    version="1.0.0",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post(
    "/plans",
    response_model=PlanResponse,
    responses={
        409: {"description": "无解：任何工艺档组合都无法保证全部间隙合格"},
        422: {"description": "请求体不合法（重复 ID、缺失引用、空档、倒置区间等）"},
    },
)
def create_plan(req: PlanRequest) -> PlanResponse:
    validate_request(req)
    try:
        result = solve(req)
    except InfeasibleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return PlanResponse.model_validate(result)


# Pydantic 校验错误（请求体结构/类型/取值范围）由 FastAPI 自动返回 422；
# 语义校验（HTTPException 422）沿用其 detail 结构。此处仅统一 500 文案。
@app.exception_handler(RuntimeError)
async def runtime_error_handler(_request, exc: RuntimeError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc)})

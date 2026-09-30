"""FastAPI application exposing POST /plans."""

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from .models import PlanRequest, PlanResponse
from .solver import InfeasiblePlanError, SolverFailureError, solve_plan
from .validation import validate_request

app = FastAPI(
    title="Mechanical Assembly Tolerance Planning Service",
    description=(
        "Select one process档 per part minimizing total cost while every "
        "clearance stays inside its allowed interval under all batch-group "
        "and residual perturbations."
    ),
    version="1.0.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/plans", response_model=PlanResponse)
def create_plan(req: PlanRequest) -> PlanResponse:
    # Pydantic shape/type errors already produce 422; the validator below
    # covers duplicate ids, missing references and inverted intervals.
    validate_request(req)
    try:
        return solve_plan(req)
    except InfeasiblePlanError:
        raise HTTPException(
            status_code=409,
            detail="no process档 selection keeps every gap within its interval",
        )
    except SolverFailureError as exc:
        return JSONResponse(
            status_code=500,
            content={"message": "solver failed to produce a result", "detail": str(exc)},
        )

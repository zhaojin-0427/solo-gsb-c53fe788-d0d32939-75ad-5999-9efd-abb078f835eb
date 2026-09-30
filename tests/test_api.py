"""服务端端到端测试，所有期望值均按公式手工核算。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _sample_request(**overrides):
    req = {
        "batch_groups": [
            {"id": "GA", "shift_limit": 20},
            {"id": "GB", "shift_limit": 15},
        ],
        "parts": [
            {
                "id": "P1",
                "nominal_size": 10000,
                "batch_group_id": "GA",
                "options": [
                    {"id": "cheap", "cost": 50, "residual_limit": 40},
                    {"id": "fine", "cost": 120, "residual_limit": 10},
                ],
            },
            {
                "id": "P2",
                "nominal_size": 5000,
                "batch_group_id": "GB",
                "options": [
                    {"id": "std", "cost": 30, "residual_limit": 60},
                    {"id": "prec", "cost": 80, "residual_limit": 15},
                ],
            },
        ],
        "gaps": [
            {
                "id": "GAP1",
                "constant": -12000,
                "coefficients": [
                    {"part_id": "P1", "value": 1},
                    {"part_id": "P2", "value": 1},
                ],
                "allowed_low": 2850,
                "allowed_high": 3150,
            },
            {
                "id": "GAP2",
                "constant": 2000,
                "coefficients": [{"part_id": "P1", "value": -1}],
                "allowed_low": -8100,
                "allowed_high": -7900,
            },
        ],
    }
    req.update(overrides)
    return req


def test_sample_plan_optimal_and_witnesses():
    r = client.post("/plans", json=_sample_request())
    assert r.status_code == 200, r.text
    body = r.json()

    # 最优：P1=cheap(50) + P2=std(30) = 80；半宽 35+40+60=135。
    assert body["total_cost"] == 80
    assert body["selections"] == [
        {"part_id": "P1", "option_id": "cheap"},
        {"part_id": "P2", "option_id": "std"},
    ]

    g1 = body["gaps"][0]
    assert g1["gap_id"] == "GAP1"
    assert g1["nominal_center"] == 3000
    assert g1["min_value"] == 2865
    assert g1["max_value"] == 3135
    assert g1["min_witness"] == {
        "batch_group_shifts": {"GA": -20, "GB": -15},
        "part_residuals": {"P1": -40, "P2": -60},
    }
    assert g1["max_witness"] == {
        "batch_group_shifts": {"GA": 20, "GB": 15},
        "part_residuals": {"P1": 40, "P2": 60},
    }

    # GAP2 只含 P1，系数 -1；最小间隙见证方向全部反转。
    g2 = body["gaps"][1]
    assert g2["nominal_center"] == -8000
    assert g2["min_value"] == -8060
    assert g2["max_value"] == -7940
    assert g2["min_witness"] == {
        "batch_group_shifts": {"GA": 20, "GB": 0},
        "part_residuals": {"P1": 40, "P2": 0},
    }
    assert g2["max_witness"] == {
        "batch_group_shifts": {"GA": -20, "GB": 0},
        "part_residuals": {"P1": -40, "P2": 0},
    }


def test_tie_break_lexicographic_with_tradeoff():
    # 两件零件各有同成本两档；残差半宽之和 ≤ 10。
    # 可行方案 (x1,y2)/(x2,y1)/(x2,y2)，字典序最小向量为 [x1, y2]。
    req = {
        "batch_groups": [{"id": "G", "shift_limit": 0}],
        "parts": [
            {
                "id": "part-A",
                "nominal_size": 0,
                "batch_group_id": "G",
                "options": [
                    {"id": "opt-x1", "cost": 5, "residual_limit": 10},
                    {"id": "opt-x2", "cost": 5, "residual_limit": 0},
                ],
            },
            {
                "id": "part-B",
                "nominal_size": 0,
                "batch_group_id": "G",
                "options": [
                    {"id": "opt-y1", "cost": 5, "residual_limit": 10},
                    {"id": "opt-y2", "cost": 5, "residual_limit": 0},
                ],
            },
        ],
        "gaps": [
            {
                "id": "G1",
                "constant": 0,
                "coefficients": [
                    {"part_id": "part-A", "value": 1},
                    {"part_id": "part-B", "value": 1},
                ],
                "allowed_low": -10,
                "allowed_high": 10,
            }
        ],
    }
    r = client.post("/plans", json=req)
    assert r.status_code == 200, r.text
    assert r.json()["selections"] == [
        {"part_id": "part-A", "option_id": "opt-x1"},
        {"part_id": "part-B", "option_id": "opt-y2"},
    ]


def test_tie_break_skips_infeasible_smaller_id():
    # opt-A 残差 10 超出 [-5,5]，虽 ID 小也必须选 opt-B。
    req = {
        "batch_groups": [{"id": "G", "shift_limit": 0}],
        "parts": [
            {
                "id": "P",
                "nominal_size": 0,
                "batch_group_id": "G",
                "options": [
                    {"id": "opt-A", "cost": 10, "residual_limit": 10},
                    {"id": "opt-B", "cost": 10, "residual_limit": 5},
                ],
            }
        ],
        "gaps": [
            {
                "id": "G",
                "constant": 0,
                "coefficients": [{"part_id": "P", "value": 1}],
                "allowed_low": -5,
                "allowed_high": 5,
            }
        ],
    }
    r = client.post("/plans", json=req)
    assert r.status_code == 200, r.text
    assert r.json()["selections"][0]["option_id"] == "opt-B"


def test_infeasible_residual_returns_409():
    req = {
        "batch_groups": [{"id": "G", "shift_limit": 0}],
        "parts": [
            {
                "id": "P",
                "nominal_size": 0,
                "batch_group_id": "G",
                "options": [{"id": "o", "cost": 1, "residual_limit": 10}],
            }
        ],
        "gaps": [
            {
                "id": "G",
                "constant": 0,
                "coefficients": [{"part_id": "P", "value": 1}],
                "allowed_low": -5,
                "allowed_high": 5,
            }
        ],
    }
    r = client.post("/plans", json=req)
    assert r.status_code == 409


def test_infeasible_group_shift_unavoidable_returns_409():
    # 残差可为 0，但批组偏移 ±10 本身就冲破 [-5,5]：与选档无关，必无解。
    req = {
        "batch_groups": [{"id": "G", "shift_limit": 10}],
        "parts": [
            {
                "id": "P",
                "nominal_size": 0,
                "batch_group_id": "G",
                "options": [{"id": "o", "cost": 1, "residual_limit": 0}],
            }
        ],
        "gaps": [
            {
                "id": "G",
                "constant": 0,
                "coefficients": [{"part_id": "P", "value": 1}],
                "allowed_low": -5,
                "allowed_high": 5,
            }
        ],
    }
    r = client.post("/plans", json=req)
    assert r.status_code == 409


def test_duplicate_part_id_returns_422():
    req = _sample_request()
    req["parts"][1]["id"] = "P1"
    r = client.post("/plans", json=req)
    assert r.status_code == 422


def test_missing_group_reference_returns_422():
    req = _sample_request()
    req["parts"][0]["batch_group_id"] = "NOPE"
    r = client.post("/plans", json=req)
    assert r.status_code == 422


def test_missing_part_reference_in_gap_returns_422():
    req = _sample_request()
    req["gaps"][0]["coefficients"].append({"part_id": "PX", "value": 1})
    r = client.post("/plans", json=req)
    assert r.status_code == 422


def test_empty_options_returns_422():
    req = _sample_request()
    req["parts"][0]["options"] = []
    r = client.post("/plans", json=req)
    assert r.status_code == 422


def test_inverted_interval_returns_422():
    req = _sample_request()
    req["gaps"][0]["allowed_low"], req["gaps"][0]["allowed_high"] = 10, 0
    r = client.post("/plans", json=req)
    assert r.status_code == 422


def test_negative_cost_returns_422():
    req = _sample_request()
    req["parts"][0]["options"][0]["cost"] = -1
    r = client.post("/plans", json=req)
    assert r.status_code == 422


def test_duplicate_option_id_returns_422():
    req = _sample_request()
    req["parts"][0]["options"][1]["id"] = "cheap"
    r = client.post("/plans", json=req)
    assert r.status_code == 422


def test_duplicate_coefficient_returns_422():
    req = _sample_request()
    req["gaps"][0]["coefficients"].append({"part_id": "P1", "value": 2})
    r = client.post("/plans", json=req)
    assert r.status_code == 422


def test_cost_priority_over_ids():
    # ID 更小的 aaa 更贵；成本优先，必须选 zzz。
    req = {
        "batch_groups": [{"id": "G", "shift_limit": 0}],
        "parts": [
            {
                "id": "P",
                "nominal_size": 0,
                "batch_group_id": "G",
                "options": [
                    {"id": "zzz", "cost": 5, "residual_limit": 0},
                    {"id": "aaa", "cost": 10, "residual_limit": 0},
                ],
            }
        ],
        "gaps": [
            {
                "id": "G",
                "constant": 0,
                "coefficients": [{"part_id": "P", "value": 1}],
                "allowed_low": -100,
                "allowed_high": 100,
            }
        ],
    }
    r = client.post("/plans", json=req)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["selections"][0]["option_id"] == "zzz"
    assert body["total_cost"] == 5


def test_zero_coefficient_constant_gap():
    # 无系数项：间隙恒等于常数，见证全部为 0。
    req = {
        "batch_groups": [{"id": "G", "shift_limit": 10}],
        "parts": [
            {
                "id": "P",
                "nominal_size": 0,
                "batch_group_id": "G",
                "options": [{"id": "o", "cost": 1, "residual_limit": 5}],
            }
        ],
        "gaps": [
            {
                "id": "G",
                "constant": 42,
                "coefficients": [],
                "allowed_low": 42,
                "allowed_high": 42,
            }
        ],
    }
    r = client.post("/plans", json=req)
    assert r.status_code == 200, r.text
    g = r.json()["gaps"][0]
    assert g["nominal_center"] == g["min_value"] == g["max_value"] == 42
    assert g["min_witness"] == {
        "batch_group_shifts": {"G": 0},
        "part_residuals": {"P": 0},
    }


def test_health():
    assert client.get("/health").json() == {"status": "ok"}

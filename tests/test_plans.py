"""End-to-end tests for POST /plans."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _example_payload(**overrides):
    payload = {
        "groups": [
            {"id": "g1", "offset_bound": 4},
            {"id": "g2", "offset_bound": 3},
        ],
        "parts": [
            {
                "id": "P1",
                "nominal_size": 10000,
                "batch_group_id": "g1",
                "options": [
                    {"id": "A1", "cost": 10, "residual_bound": 5},
                    {"id": "A2", "cost": 4, "residual_bound": 15},
                ],
            },
            {
                "id": "P2",
                "nominal_size": 20000,
                "batch_group_id": "g2",
                "options": [
                    {"id": "B1", "cost": 20, "residual_bound": 6},
                    {"id": "B2", "cost": 8, "residual_bound": 20},
                ],
            },
            {
                "id": "P3",
                "nominal_size": 15000,
                "batch_group_id": "g2",
                "options": [
                    {"id": "C1", "cost": 12, "residual_bound": 8},
                    {"id": "C2", "cost": 3, "residual_bound": 25},
                ],
            },
        ],
        "gaps": [
            {
                "id": "G1",
                "constant": 30040,
                "coefficients": {"P1": 2, "P2": -1, "P3": -2},
                "lower": -63,
                "upper": 143,
            },
            {
                "id": "G2",
                "constant": -4960,
                "coefficients": {"P1": 1, "P2": 2, "P3": -3},
                "lower": -116,
                "upper": 196,
            },
        ],
    }
    payload.update(overrides)
    return payload


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_example_plan_optimum_and_extremes():
    r = client.post("/plans", json=_example_payload())
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["total_cost"] == 21
    assert [(p["part_id"], p["option_id"]) for p in body["plan"]] == [
        ("P1", "A1"),
        ("P2", "B2"),
        ("P3", "C2"),
    ]
    assert [p["cost"] for p in body["plan"]] == [10, 8, 3]

    extremes = {g["gap_id"]: (g["minimum"], g["maximum"]) for g in body["gap_extremes"]}
    assert extremes == {"G1": (-57, 137), "G2": (-87, 167)}

    witnesses = {(w["gap_id"], w["extreme"]): w for w in body["witnesses"]}
    assert witnesses[("G1", "minimum")]["value"] == -57
    assert witnesses[("G1", "maximum")]["value"] == 137
    assert witnesses[("G2", "minimum")]["value"] == -87
    assert witnesses[("G2", "maximum")]["value"] == 167

    assert witnesses[("G1", "minimum")]["group_offsets"] == {"g1": -4, "g2": 3}
    assert witnesses[("G1", "minimum")]["part_residuals"] == {
        "P1": -5,
        "P2": 20,
        "P3": 25,
    }
    assert witnesses[("G1", "maximum")]["group_offsets"] == {"g1": 4, "g2": -3}
    assert witnesses[("G1", "maximum")]["part_residuals"] == {
        "P1": 5,
        "P2": -20,
        "P3": -25,
    }
    assert witnesses[("G2", "minimum")]["group_offsets"] == {"g1": -4, "g2": 3}
    assert witnesses[("G2", "minimum")]["part_residuals"] == {
        "P1": -5,
        "P2": -20,
        "P3": 25,
    }
    assert witnesses[("G2", "maximum")]["group_offsets"] == {"g1": 4, "g2": -3}
    assert witnesses[("G2", "maximum")]["part_residuals"] == {
        "P1": 5,
        "P2": 20,
        "P3": -25,
    }


def test_witnesses_actually_reproduce_extremes():
    body = client.post("/plans", json=_example_payload()).json()
    payload = _example_payload()
    parts = {p["id"]: p for p in payload["parts"]}
    groups = {g["id"]: g for g in payload["groups"]}

    for gap in payload["gaps"]:
        for extreme in ("minimum", "maximum"):
            w = next(
                w
                for w in body["witnesses"]
                if w["gap_id"] == gap["id"] and w["extreme"] == extreme
            )
            # Witness perturbations must stay inside their symmetric bounds.
            for gid, g in groups.items():
                assert abs(w["group_offsets"][gid]) <= g["offset_bound"]
            for pid, p in parts.items():
                chosen = next(o for o in p["options"] if o["id"] ==
                              next(x["option_id"] for x in body["plan"] if x["part_id"] == pid))
                assert abs(w["part_residuals"][pid]) <= chosen["residual_bound"]

            value = gap["constant"]
            for pid, a in gap["coefficients"].items():
                p = parts[pid]
                actual = (
                    p["nominal_size"]
                    + w["group_offsets"][p["batch_group_id"]]
                    + w["part_residuals"][pid]
                )
                value += a * actual
            assert value == w["value"]


def test_lexicographic_tie_break_picks_smallest_option_ids():
    # Both parts: cheap option (cost 1) leaves too much residual spread for
    # the tight gap, so cost-2 options are mandatory.  Each part has two
    # cost-2 options; the lexicographically smallest pair must be selected.
    payload = {
        "groups": [{"id": "g1", "offset_bound": 0}],
        "parts": [
            {
                "id": "X",
                "nominal_size": 0,
                "batch_group_id": "g1",
                "options": [
                    {"id": "cheap", "cost": 1, "residual_bound": 100},
                    {"id": "z2", "cost": 2, "residual_bound": 0},
                    {"id": "a2", "cost": 2, "residual_bound": 0},
                ],
            },
            {
                "id": "Y",
                "nominal_size": 0,
                "batch_group_id": "g1",
                "options": [
                    {"id": "cheap", "cost": 1, "residual_bound": 100},
                    {"id": "m2", "cost": 2, "residual_bound": 0},
                    {"id": "b2", "cost": 2, "residual_bound": 0},
                ],
            },
        ],
        "gaps": [
            {
                "id": "tight",
                "constant": 0,
                "coefficients": {"X": 1, "Y": 1},
                "lower": 0,
                "upper": 0,
            }
        ],
    }
    r = client.post("/plans", json=payload)
    assert r.status_code == 200, r.text
    plan = [(p["part_id"], p["option_id"]) for p in r.json()["plan"]]
    assert plan == [("X", "a2"), ("Y", "b2")]
    assert r.json()["total_cost"] == 4


def test_infeasible_returns_409():
    payload = _example_payload()
    # Tighten G1 so that even the tightest options (residual spread 46,
    # group spread 17, total half-width 63) cannot fit the interval.
    payload["gaps"][0]["lower"] = -20
    payload["gaps"][0]["upper"] = 60
    r = client.post("/plans", json=payload)
    assert r.status_code == 409


def test_infeasible_from_group_disturbance_alone_returns_409():
    # Half-width from the group bound alone (2 * 10 = 20) exceeds tolerance.
    payload = {
        "groups": [{"id": "g1", "offset_bound": 10}],
        "parts": [
            {
                "id": "P1",
                "nominal_size": 0,
                "batch_group_id": "g1",
                "options": [{"id": "O1", "cost": 0, "residual_bound": 0}],
            }
        ],
        "gaps": [
            {
                "id": "G1",
                "constant": 0,
                "coefficients": {"P1": 2},
                "lower": -10,
                "upper": 10,
            }
        ],
    }
    r = client.post("/plans", json=payload)
    assert r.status_code == 409


def test_duplicate_part_id_returns_422():
    payload = _example_payload()
    payload["parts"][2]["id"] = "P1"
    r = client.post("/plans", json=payload)
    assert r.status_code == 422


def test_missing_group_reference_returns_422():
    payload = _example_payload()
    payload["parts"][0]["batch_group_id"] = "nope"
    r = client.post("/plans", json=payload)
    assert r.status_code == 422
    assert "unknown group" in r.text


def test_part_without_options_returns_422():
    payload = _example_payload()
    payload["parts"][0]["options"] = []
    r = client.post("/plans", json=payload)
    assert r.status_code == 422


def test_duplicate_option_id_returns_422():
    payload = _example_payload()
    payload["parts"][0]["options"][1]["id"] = "A1"
    r = client.post("/plans", json=payload)
    assert r.status_code == 422
    assert "duplicate option id" in r.text


def test_inverted_interval_returns_422():
    payload = _example_payload()
    payload["gaps"][0]["lower"], payload["gaps"][0]["upper"] = 70, 10
    r = client.post("/plans", json=payload)
    assert r.status_code == 422
    assert "inverted interval" in r.text


def test_gap_referencing_unknown_part_returns_422():
    payload = _example_payload()
    payload["gaps"][0]["coefficients"]["P9"] = 1
    r = client.post("/plans", json=payload)
    assert r.status_code == 422
    assert "unknown part" in r.text


def test_duplicate_gap_id_returns_422():
    payload = _example_payload()
    payload["gaps"][1]["id"] = "G1"
    r = client.post("/plans", json=payload)
    assert r.status_code == 422


def test_negative_cost_returns_422():
    payload = _example_payload()
    payload["parts"][0]["options"][0]["cost"] = -1
    r = client.post("/plans", json=payload)
    assert r.status_code == 422


def test_boolean_instead_of_integer_returns_422():
    payload = _example_payload()
    payload["parts"][0]["nominal_size"] = True
    r = client.post("/plans", json=payload)
    assert r.status_code == 422


def test_empty_lists_returns_422():
    r = client.post("/plans", json={"groups": [], "parts": [], "gaps": []})
    assert r.status_code == 422


def test_sparse_coefficients_and_zero_coefficient_allowed():
    payload = {
        "groups": [{"id": "g1", "offset_bound": 0}],
        "parts": [
            {
                "id": "P1",
                "nominal_size": 5,
                "batch_group_id": "g1",
                "options": [{"id": "O1", "cost": 7, "residual_bound": 0}],
            }
        ],
        "gaps": [
            {
                "id": "G1",
                "constant": 100,
                "coefficients": {},
                "lower": 100,
                "upper": 100,
            }
        ],
    }
    r = client.post("/plans", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_cost"] == 7
    assert body["gap_extremes"][0] == {"gap_id": "G1", "minimum": 100, "maximum": 100}
    w = body["witnesses"][0]
    assert w["group_offsets"] == {"g1": 0}
    assert w["part_residuals"] == {"P1": 0}

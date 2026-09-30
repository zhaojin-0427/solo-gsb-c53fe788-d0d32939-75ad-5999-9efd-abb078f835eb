# 机械装配公差方案服务

为机械装配中的每件零件选择一个**工艺档**，使全部装配间隙在批组共同偏移与
独立残差的**任意最坏组合扰动**下都落在允许闭区间内，并最小化工艺档总成本。

- Python 3.11 · FastAPI · OR-Tools CP-SAT
- 尺寸量均为**整数微米**；偏移上限、残差上限、成本均为非负整数
- 系数、名义尺寸、常数与区间端点可为负整数

## 目录

- [启动](#启动)
- [访问地址](#访问地址)
- [接口说明](#接口说明)
- [请求示例](#请求示例)
- [响应示例](#响应示例)
- [判定与求解规则](#判定与求解规则)
- [错误码](#错误码)
- [本地开发与测试](#本地开发与测试)

## 启动

### Docker Compose 一键启动（推荐）

```bash
docker compose up --build -d
```

### 本地直接启动

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 访问地址

服务启动后：

| 用途 | 地址 |
| --- | --- |
| 求解接口 `POST /plans` | <http://localhost:8000/plans> |
| 健康检查 `GET /health` | <http://localhost:8000/health> |
| Swagger 交互文档 | <http://localhost:8000/docs> |
| OpenAPI 描述 | <http://localhost:8000/openapi.json> |

## 接口说明

`POST /plans`，请求体 JSON：

- `batch_groups`（非空）：批组列表
  - `id`：批组 ID
  - `shift_limit`：批组共同偏移对称上限，偏移在 `[-shift_limit, shift_limit]`
- `parts`（非空）：零件列表
  - `id`：零件 ID
  - `nominal_size`：名义尺寸（整数微米）
  - `batch_group_id`：所属批组 ID
  - `options`（非空）：工艺档列表，每件零件恰好选一档
    - `id`：工艺档 ID
    - `cost`：该档成本（非负整数）
    - `residual_limit`：该档独立残差对称上限，残差在
      `[-residual_limit, residual_limit]`
- `gaps`（非空）：装配间隙列表
  - `id`：间隙 ID
  - `constant`：间隙常数
  - `coefficients`：零件整数系数项 `[{ "part_id", "value" }, ...]`，
    每个零件在同一间隙中至多出现一次；不出现等价于系数 0
  - `allowed_low` / `allowed_high`：允许闭区间端点，须满足
    `allowed_low <= allowed_high`

同一零件 ID、批组 ID、间隙 ID 不得重复；零件内工艺档 ID 不得重复；
引用必须存在。任一违反返回 `422`。

## 请求示例

```bash
curl -X POST http://localhost:8000/plans \
  -H 'Content-Type: application/json' \
  -d @examples/request.json
```

`examples/request.json`：

```json
{
  "batch_groups": [
    {"id": "GA", "shift_limit": 20},
    {"id": "GB", "shift_limit": 15}
  ],
  "parts": [
    {
      "id": "P1",
      "nominal_size": 10000,
      "batch_group_id": "GA",
      "options": [
        {"id": "cheap", "cost": 50, "residual_limit": 40},
        {"id": "fine",  "cost": 120, "residual_limit": 10}
      ]
    },
    {
      "id": "P2",
      "nominal_size": 5000,
      "batch_group_id": "GB",
      "options": [
        {"id": "std",  "cost": 30, "residual_limit": 60},
        {"id": "prec", "cost": 80, "residual_limit": 15}
      ]
    }
  ],
  "gaps": [
    {
      "id": "GAP1",
      "constant": -12000,
      "coefficients": [
        {"part_id": "P1", "value": 1},
        {"part_id": "P2", "value": 1}
      ],
      "allowed_low": 2850,
      "allowed_high": 3150
    },
    {
      "id": "GAP2",
      "constant": 2000,
      "coefficients": [
        {"part_id": "P1", "value": -1}
      ],
      "allowed_low": -8100,
      "allowed_high": -7900
    }
  ]
}
```

## 响应示例

上例的实际响应（`200 OK`）：

```json
{
  "selections": [
    {"part_id": "P1", "option_id": "cheap"},
    {"part_id": "P2", "option_id": "std"}
  ],
  "total_cost": 80,
  "gaps": [
    {
      "gap_id": "GAP1",
      "allowed_low": 2850,
      "allowed_high": 3150,
      "nominal_center": 3000,
      "min_value": 2865,
      "max_value": 3135,
      "min_witness": {
        "batch_group_shifts": {"GA": -20, "GB": -15},
        "part_residuals": {"P1": -40, "P2": -60}
      },
      "max_witness": {
        "batch_group_shifts": {"GA": 20, "GB": 15},
        "part_residuals": {"P1": 40, "P2": 60}
      }
    },
    {
      "gap_id": "GAP2",
      "allowed_low": -8100,
      "allowed_high": -7900,
      "nominal_center": -8000,
      "min_value": -8060,
      "max_value": -7940,
      "min_witness": {
        "batch_group_shifts": {"GA": 20, "GB": 0},
        "part_residuals": {"P1": 40, "P2": 0}
      },
      "max_witness": {
        "batch_group_shifts": {"GA": -20, "GB": 0},
        "part_residuals": {"P1": -40, "P2": 0}
      }
    }
  ]
}
```

字段说明：

- `selections`：每件零件选中的工艺档，按零件 ID 升序排列。
- `total_cost`：方案总成本（整数）。
- `gaps[].nominal_center`：名义中心 `constant + Σ 系数×名义尺寸`，与选档无关。
- `gaps[].min_value` / `max_value`：在所选工艺档下，全部扰动空间内间隙的
  **精确**最小值 / 最大值（整数，对称包络）。
- `gaps[].min_witness` / `max_witness`：取到对应极值时的扰动见证，
  给出每个批组的共同偏移与每件零件的独立残差（未参与该间隙的项为 0）。
  将见证代入
  `常数 + Σ 系数 × (名义尺寸 + 批组偏移 + 残差)`
  恰好得到 `min_value` / `max_value`。

## 判定与求解规则

每件零件选一档后，实际尺寸：

```
actual_i = nominal_i + s_group(i) + r_i
s_g ∈ [-L_g, L_g]    （批组共享，组内所有零件同值）
r_i ∈ [-R_i, R_i]    （该零件所选中档的残差上限）
```

间隙：

```
gap = constant + Σ c_i × actual_i
    = nominal_center + Σ c_i × s_group(i) + Σ c_i × r_i
```

由于偏移与残差可在对称区间内**任意且同时**变化，间隙的精确极值为

```
min = nominal_center − H，max = nominal_center + H
H = Σ 批组 |该间隙在该批组的系数和| × L_g
  + Σ 零件 |c_i| × R_i(所选档)
```

方案合格当且仅当 `allowed_low ≤ min` 且 `max ≤ allowed_high`（所有间隙）。
批组贡献与选档无关；可通过选择更小残差上限的工艺档压缩的是残差贡献。

求解为带“每零件恰好选一档”的整数约束规划（OR-Tools CP-SAT）：

1. 最小化 `total_cost`；
2. 在成本并列的方案中，选择**按零件 ID 排序后的工艺档 ID 向量字典序最小**
   的方案（逐位置最小化工艺档 ID 升序序号实现）。

## 错误码

| 状态码 | 触发条件 |
| --- | --- |
| `200` | 存在合格方案，返回最优方案 |
| `409` | 无解：任何工艺档组合都无法在全部扰动下满足全部间隙 |
| `422` | 请求体不合法：结构/类型错误、非负字段为负、重复 ID（批组/零件/间隙/零件内工艺档）、缺失引用（零件引用批组、间隙系数引用零件）、空档（零件工艺档为空）、同时间隙系数重复、区间倒置 `allowed_low > allowed_high`、空列表、整数越界 |

409 响应示例：

```json
{"detail": "任何工艺档组合都无法在全部扰动下满足间隙要求"}
```

422 响应示例（语义错误）：

```json
{"detail": "零件 P 引用了不存在的批组: ZZ"}
```

## 本地开发与测试

```bash
pip install -r requirements.txt
pip install pytest httpx
pytest -q
```

测试覆盖：最优成本与精确极值/见证、成本并列字典序（含更小 ID 不可行而
跳过的情况）、批组偏移无法补偿的 409、残差超限的 409，以及各类 422。

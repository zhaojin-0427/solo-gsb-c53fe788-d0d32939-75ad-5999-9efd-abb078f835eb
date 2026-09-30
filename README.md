# 机械装配公差方案服务 (Mechanical Assembly Tolerance Planner)

基于 **Python + FastAPI + Google OR-Tools (CP-SAT)** 的 HTTP 服务。给定零件、
批组、工艺档和装配间隙约束，为每个零件选择一档工艺，使**总成本最低**，并且
在批组共同偏移与独立残差的**所有扰动组合**下，每条间隙都落在允许闭区间内。

- 所有尺寸量均为**整数微米**；各上限与成本均为非负整数。
- 每件零件实际尺寸：`实际尺寸 = 名义尺寸 + 批组共同偏移 + 独立残差`
  - 批组共同偏移在 `[-offset_bound, +offset_bound]` 内任意变化，组内所有零件共享；
  - 独立残差在所选工艺档的 `[-residual_bound, +residual_bound]` 内任意变化。
- 每条间隙：`间隙 = constant + Σ 零件系数 × 实际尺寸`。
- 成本并列时，按零件 ID 排序后的工艺档 ID 向量取**字典序最小**方案。

## 一键启动（Docker Compose）

```bash
docker compose up --build -d
```

服务启动后访问地址：

| 用途 | 地址 |
| --- | --- |
| 规划接口 | http://localhost:8000/plans |
| 健康检查 | http://localhost:8000/health |
| Swagger 交互文档 | http://localhost:8000/docs |
| OpenAPI Schema | http://localhost:8000/openapi.json |

停止服务：

```bash
docker compose down
```

## 请求

`POST /plans`，`Content-Type: application/json`：

| 字段 | 说明 |
| --- | --- |
| `groups[].id` / `groups[].offset_bound` | 批组 ID 与共同偏移对称上限（非负整数） |
| `parts[].id` | 零件 ID（非空字符串，不可重复） |
| `parts[].nominal_size` | 名义尺寸（整数微米） |
| `parts[].batch_group_id` | 所属批组，必须引用已声明的批组 |
| `parts[].options[]` | 至少一档；每档含 `id`、非负整数 `cost` 与 `residual_bound`；同零件内档 ID 不可重复 |
| `gaps[].id` | 间隙 ID（不可重复） |
| `gaps[].constant` | 间隙常数项（整数） |
| `gaps[].coefficients` | 零件 ID → 整数系数的稀疏映射，键必须引用已声明零件；零系数可省略 |
| `gaps[].lower` / `gaps[].upper` | 允许闭区间，要求 `lower <= upper` |

### 请求示例

文件见 [`examples/request.json`](examples/request.json)：

```bash
curl -X POST http://localhost:8000/plans \
  -H 'Content-Type: application/json' \
  -d @examples/request.json
```

```json
{
  "groups": [
    { "id": "g1", "offset_bound": 4 },
    { "id": "g2", "offset_bound": 3 }
  ],
  "parts": [
    {
      "id": "P1",
      "nominal_size": 10000,
      "batch_group_id": "g1",
      "options": [
        { "id": "A1", "cost": 10, "residual_bound": 5 },
        { "id": "A2", "cost": 4, "residual_bound": 15 }
      ]
    },
    {
      "id": "P2",
      "nominal_size": 20000,
      "batch_group_id": "g2",
      "options": [
        { "id": "B1", "cost": 20, "residual_bound": 6 },
        { "id": "B2", "cost": 8, "residual_bound": 20 }
      ]
    },
    {
      "id": "P3",
      "nominal_size": 15000,
      "batch_group_id": "g2",
      "options": [
        { "id": "C1", "cost": 12, "residual_bound": 8 },
        { "id": "C2", "cost": 3, "residual_bound": 25 }
      ]
    }
  ],
  "gaps": [
    {
      "id": "G1",
      "constant": 30040,
      "coefficients": { "P1": 2, "P2": -1, "P3": -2 },
      "lower": -63,
      "upper": 143
    },
    {
      "id": "G2",
      "constant": -4960,
      "coefficients": { "P1": 1, "P2": 2, "P3": -3 },
      "lower": -116,
      "upper": 196
    }
  ]
}
```

## 响应（200）

文件见 [`examples/response.json`](examples/response.json)，以下为该请求的
**实际返回内容**：

```json
{
  "plan": [
    { "part_id": "P1", "option_id": "A1", "cost": 10 },
    { "part_id": "P2", "option_id": "B2", "cost": 8 },
    { "part_id": "P3", "option_id": "C2", "cost": 3 }
  ],
  "total_cost": 21,
  "gap_extremes": [
    { "gap_id": "G1", "minimum": -57, "maximum": 137 },
    { "gap_id": "G2", "minimum": -87, "maximum": 167 }
  ],
  "witnesses": [
    {
      "gap_id": "G1",
      "extreme": "minimum",
      "group_offsets": { "g1": -4, "g2": 3 },
      "part_residuals": { "P1": -5, "P2": 20, "P3": 25 },
      "value": -57
    },
    {
      "gap_id": "G1",
      "extreme": "maximum",
      "group_offsets": { "g1": 4, "g2": -3 },
      "part_residuals": { "P1": 5, "P2": -20, "P3": -25 },
      "value": 137
    },
    {
      "gap_id": "G2",
      "extreme": "minimum",
      "group_offsets": { "g1": -4, "g2": 3 },
      "part_residuals": { "P1": -5, "P2": -20, "P3": 25 },
      "value": -87
    },
    {
      "gap_id": "G2",
      "extreme": "maximum",
      "group_offsets": { "g1": 4, "g2": -3 },
      "part_residuals": { "P1": 5, "P2": 20, "P3": -25 },
      "value": 167
    }
  ]
}
```

- `plan`：每个零件选定的工艺档及该档成本，按零件 ID 排序。
- `total_cost`：方案总成本。
- `gap_extremes`：各间隙在**全部扰动下**的精确最小值与最大值（整数），均位于区间内。
- `witnesses`：取得每个极值的扰动见证——给出每组的共同偏移与每件零件的独立残差，
  以及代入后的间隙值 `value`。见证偏移/残差均不超过各自上限。

## 状态码

| 状态码 | 场景 |
| --- | --- |
| `200` | 求得最优方案 |
| `409 Conflict` | 无任何工艺档选择能让所有间隙在全部扰动下合格 |
| `422 Unprocessable Entity` | 请求结构/类型错误，或存在重复 ID、缺失引用（零件引用未知批组、间隙引用未知零件）、零件空档、倒置区间（`lower > upper`） |
| `500` | 求解器在时限内未能给出可证明的结果 |

`409` 响应示例：

```json
{ "detail": "no process档 selection keeps every gap within its interval" }
```

`422` 语义错误响应示例（重复 ID / 缺失引用 / 空档 / 倒置区间）：

```json
{
  "detail": {
    "message": "invalid tolerance plan request",
    "errors": ["gap 'G1' has inverted interval [70, -63]"]
  }
}
```

## 求解原理

对间隙 \(j\)，记批组 \(g\) 在该间隙上的系数和为
\(A_{jg}=\sum_{p\in g} a_{jp}\)，名义间隙为
\(N_j=c_j+\sum_p a_{jp}\cdot\text{nominal}_p\)。由于区间对称，间隙的精确极值为：

\[
G_j^{\min/\max}=N_j\ \mp\ \sum_g |A_{jg}|B_g\ \mp\ \sum_p |a_{jp}|\,r_{o_p}
\]

其中 \(B_g\) 为批组偏移上限，\(r_{o_p}\) 为零件 \(p\) 所选工艺档的残差上限。
极值可由把每个扰动取到对应符号的端点同时达到，因此“所有间隙在所有扰动下合格”
等价于对每条间隙施加一个关于所选残差上限的线性约束。问题被建模为 CP-SAT 的
有限域整数规划：每件零件一个 `ExactlyOne` 档位选择变量，目标为总成本最小。
求得最优成本后，按零件 ID 顺序逐位固定字典序最小的可行档 ID 向量。

## 本地开发（不使用 Docker）

需要 Python 3.11+：

```bash
python -m pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

运行测试：

```bash
python -m pip install pytest httpx
PYTHONPATH=. python -m pytest -q
```

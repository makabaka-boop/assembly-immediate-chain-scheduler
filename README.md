# 低换型装配线排程 API (Low-Changeover Assembly Line Scheduler)

纯后端服务。给定工单及其配方族（family）与 `before → after` 前置关系，输出一个
满足全部前置的完整顺序，优化目标为：

1. **最少换型**：相邻工单 family 不同的次数最少（第 1 个工单不计换型）；
2. 并列时，按工单 id 的 **UTF-8 字节序**取字典序最小的顺序；
3. 列出每次换型的 1-based 位置及前后工单。

算法为**子集状态动态规划**（精确解，非贪心、非启发式）：

```
dp(mask, f) = 已放置 mask 中的工单、最后一个工单 family 为 f 时，
              完成剩余工单所需的最少换型次数
```

n ≤ 18，状态数 2^18 × 18，以扁平 `bytearray` 存储（最大代价 17，`INF=127`）；
随后在最优值上按 id 字节序贪心还原，得到唯一的字典序最小最优顺序。

## 运行

```bash
docker compose up --build
# API: http://localhost:8000  文档: http://localhost:8000/docs
```

本地（Python 3.12）：

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

测试：

```bash
pip install -e ".[dev]"
pytest
```

## 请求

`POST /schedule`

```json
{
  "jobs": [
    {"id": "a", "family": "X"},
    {"id": "b", "family": "X"},
    {"id": "c", "family": "Y"}
  ],
  "edges": [
    {"before": "a", "after": "b"}
  ]
}
```

约束（违反任一返回 **422**）：

- 2～18 个工单；`id` 唯一，`id`/`family` 均为非空 ASCII 字符串；
- `edges` 可省略（视为空），至多 100 条；
- 自环、重复边、引用未知工单 id、任何层级的额外字段一律 422。

## 响应

成功（200）：

```json
{
  "status": "OK",
  "order": ["a", "b", "c"],
  "changeover_count": 1,
  "changeover_positions": [3],
  "changeovers": [
    {
      "position": 3,
      "from": {"id": "b", "family": "X"},
      "to":   {"id": "c", "family": "Y"}
    }
  ]
}
```

存在依赖环（200，`status=CYCLE`；**不返回任何部分排程**）。`cycle` 是一条
真实有向环，从该环中 id 字节序最小的工单开始，相邻元素（含末→首）的每条边
都存在于输入中；自环表示为 `[id]`。

```json
{
  "status": "CYCLE",
  "cycle": ["a", "b", "c"]
}
```

## 测试策略

- `tests/test_oracle.py`：对 n≤7 的随机小图穷举所有排列，直接按题目定义
  （最少换型 + 字节序）求 oracle，逐字段对拍顺序、换型次数与位置；
  含 18 工单性能用例（<15s，实测 <1s）。
- `tests/test_cycles.py`：验证环证据每条边均存在、起点为环内最小 id、
  自环、多个环时取最小环、字节序旋转。
- `tests/test_api.py`：422 校验（自环/重复/未知引用/额外字段/数量与类型）、
  CYCLE 不含部分排程、成功载荷结构。

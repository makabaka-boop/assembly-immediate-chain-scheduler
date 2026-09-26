# 低换型装配线排程 API (Low-Changeover Assembly Line Scheduler)

纯后端服务。给定工单及其配方族（family）、`before → after` 前置关系，以及可选的
**immediate 紧邻对**（`after` 必须紧接 `before` 上料，中间不得插入其他工单），
输出一个满足全部约束的完整顺序，优化目标为：

1. **最少换型**：相邻工单 family 不同的次数最少（第 1 个工单不计换型）；
2. 并列时，按工单 id 的 **UTF-8 字节序**取字典序最小的顺序；
3. 列出每次换型的 1-based 位置及前后工单。

算法为**子集状态动态规划**（精确解，非贪心、非启发式）。校验保证每个工单至多
一个紧前项、一个紧后项，因此 immediate 对构成若干条内部次序固定的**连续链**；
每条极大链先被压缩成一个 **block**，再对所有 block 做子集 DP：

```
dp(mask, f) = 已放置 mask 中的 block、最后一个工单 family 为 f 时，
              完成剩余 block 所需的最少换型次数

放置 block B 的代价 = [B 的首个 family != f] + B 链内固定换型数
```

链内固定次序与链间前置关系在同一次 DP 中一并裁决，而不是先求无紧邻约束的
最优再移动工单补救。block 数 m ≤ n ≤ 18，状态数 2^m × family 数，以扁平
`bytearray` 存储（最大代价 17，`INF=127`）；随后在最优值上按各 block 首个
工单 id 的字节序贪心还原（不同 block 首元素必不同，故首 id 即决定字典序），
得到唯一的字典序最小最优顺序。

可行性在优化前判定：immediate 关系自身成环、前置边与所在链的固定次序矛盾、
或压缩后的 block 图成环（前置图看似无环却无法紧邻），均返回
`UNSCHEDULABLE`；原始前置图有环仍返回 `CYCLE`。

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
  ],
  "immediate": [
    {"before": "b", "after": "c"}
  ]
}
```

约束（违反任一返回 **422**）：

- 2～18 个工单；`id` 唯一，`id`/`family` 均为非空 ASCII 字符串；
- `edges` 可省略（视为空），至多 100 条；
- 自环、重复边、引用未知工单 id、任何层级的额外字段一律 422；
- `immediate` 可省略（视为空，此时行为与旧版逐项一致）；其元素与 edge 同形，
  但语义为“紧接执行”：引用未知工单、自对、重复对、或分叉（同一工单出现
  多个紧后项或多个紧前项）均整份拒绝（422）。

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

前置图无环但紧邻要求无法同时满足（200，`status=UNSCHEDULABLE`；同样**不返回
任何部分排程**）。例如 `a → x → b` 的前置链本身无环，但若还要求 `b` 紧接
`a`，则 `x` 无处安放：

```json
{
  "status": "UNSCHEDULABLE"
}
```

## 测试策略

- `tests/test_oracle.py`：对 n≤7 的随机小图穷举所有排列，直接按题目定义
  （最少换型 + 字节序）求 oracle，逐字段对拍顺序、换型次数与位置；
  含 18 工单性能用例（<15s，实测 <1s）。
- `tests/test_immediate.py`：带 immediate 对的 n≤7 排列穷举对拍（可行性、
  换型数、平局字典序，OK 与 UNSCHEDULABLE 两侧均覆盖）；定向用例覆盖链间
  前置依赖、链首字典序平局、联合优化而非事后补救、看似无环却无法紧邻、
  immediate 自成环、CYCLE 优先、以及 422（未知引用/自对/重复/分叉/额外字段）
  与省略该字段时旧响应逐项不变。
- `tests/test_cycles.py`：验证环证据每条边均存在、起点为环内最小 id、
  自环、多个环时取最小环、字节序旋转。
- `tests/test_api.py`：422 校验（自环/重复/未知引用/额外字段/数量与类型）、
  CYCLE 不含部分排程、成功载荷结构。

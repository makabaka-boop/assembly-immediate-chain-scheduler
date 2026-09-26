"""Exact low-changeover topological scheduler.

Given work orders (id, family) and ``before -> after`` precedence edges, find
a topological ordering that

1. satisfies every precedence edge,
2. minimises the number of changeovers (positions where adjacent orders have
   different families; the first order never counts),
3. among all optimal orderings, is lexicographically smallest on the order's
   UTF-8 byte sequences.

The optimiser is a subset dynamic program::

    dp(mask, f) = minimum remaining changeovers when the orders in *mask*
                  have already been placed and the family of the last placed
                  order is *f*.

With at most 18 orders there are 2**18 * 18 states, stored as a flat
``bytearray`` (max cost is 17, ``INF = 0x7F``).  The lexicographically
smallest optimum is then reconstructed greedily: at every step, try the
available next orders in ascending UTF-8 id order and pick the first one
that can still attain the DP optimum.
"""

from __future__ import annotations

from dataclasses import dataclass

INF = 0x7F  # larger than any real cost (at most n-1 <= 17)


@dataclass(frozen=True)
class Job:
    id: str
    family: str


def find_cycle(jobs: list[Job], edges: list[tuple[str, str]]) -> list[str] | None:
    """Return an actual directed cycle, or ``None`` if the graph is acyclic.

    The returned evidence is a list of node ids ``[v0, v1, ..., vk-1]`` such
    that every edge ``v_i -> v_{i+1}`` (and ``v_{k-1} -> v_0``) exists in the
    input.  Rotation starts at the smallest id (UTF-8 byte order); ties are
    broken by the remaining sequence.  Self loops yield ``[v]``.
    """
    order = sorted((j.id for j in jobs), key=lambda s: s.encode("utf-8"))
    index = {jid: i for i, jid in enumerate(order)}
    adj: list[list[int]] = [[] for _ in order]
    seen: set[tuple[int, int]] = set()
    for before, after in edges:
        u, v = index[before], index[after]
        if (u, v) not in seen:
            seen.add((u, v))
            adj[u].append(v)
    for row in adj:
        row.sort()

    WHITE, GRAY, BLACK = 0, 1, 2
    color = [WHITE] * len(order)
    stack: list[int] = []
    on_stack: dict[int, int] = {}
    best: list[int] | None = None

    def rotate(cyc: list[int]) -> list[int]:
        start = min(range(len(cyc)), key=lambda i: order[cyc[i]].encode("utf-8"))
        return cyc[start:] + cyc[:start]

    def consider(cyc: list[int]) -> None:
        nonlocal best
        c = rotate(cyc)
        key = [order[v].encode("utf-8") for v in c]
        if best is None or key < [order[v].encode("utf-8") for v in best]:
            best = c

    def dfs(u: int) -> None:
        color[u] = GRAY
        on_stack[u] = len(stack)
        stack.append(u)
        for v in adj[u]:
            if color[v] == GRAY:
                consider(stack[on_stack[v]:])
            elif color[v] == WHITE:
                dfs(v)
        stack.pop()
        del on_stack[u]
        color[u] = BLACK

    for start in range(len(order)):
        if color[start] == WHITE:
            dfs(start)

    if best is None:
        return None
    return [order[v] for v in best]


def _optimal_order(
    n: int,
    family: list[int],
    num_families: int,
    prereq: list[int],
) -> list[int]:
    """DP over subsets; returns the sequence of job indices."""
    size = 1 << n
    full = size - 1
    # dp[mask * num_families + f]
    dp = bytearray([INF]) * (size * num_families)
    # Placing the first order costs nothing regardless of its family; the
    # full-mask terminal value is 0 for every family (no orders left).
    for f in range(num_families):
        dp[full * num_families + f] = 0

    allbits = full

    for mask in range(full - 1, -1, -1):
        # Orders that may be placed next: not yet placed, all prerequisites in.
        avail = 0
        candidates = allbits ^ mask
        while candidates:
            lb = candidates & -candidates
            j = lb.bit_length() - 1
            if not (prereq[j] & ~mask):
                avail |= lb
            candidates ^= lb
        if not avail:
            continue

        # best_by_fam[f] = min dp(mask|{j}, family(j)) over available jobs j
        # of family f.
        best_by_fam = [INF] * num_families
        candidates = avail
        while candidates:
            lb = candidates & -candidates
            j = lb.bit_length() - 1
            v = dp[(mask | lb) * num_families + family[j]]
            if v < best_by_fam[family[j]]:
                best_by_fam[family[j]] = v
            candidates ^= lb

        m1 = INF  # smallest family minimum
        m1_f = -1
        m2 = INF  # smallest family minimum coming from another family
        for f, v in enumerate(best_by_fam):
            if v == INF:
                continue
            if v < m1:
                m2 = m1
                m1, m1_f = v, f
            elif v < m2:
                m2 = v

        # For each possible "last placed family" f:
        #   dp(mask, f) = min over available j of
        #                 dp(mask|{j}, family(j)) + [family(j) != f]
        # = min(best_by_fam[f], min_from_another_family + 1).
        base = mask * num_families
        for f in range(num_families):
            same = best_by_fam[f]
            other = m2 if f == m1_f else m1
            if other == INF:
                # No available job from another family.
                dp[base + f] = same
            elif same == INF:
                dp[base + f] = other + 1
            else:
                dp[base + f] = same if same <= other + 1 else other + 1

    # Greedy reconstruction with ascending UTF-8 id order.
    ids_asc = list(range(n))
    order: list[int] = []
    mask = 0
    last_f = -1
    remaining_cost: int | None = None
    while mask != full:
        avail = 0
        candidates = allbits ^ mask
        while candidates:
            lb = candidates & -candidates
            j = lb.bit_length() - 1
            if not (prereq[j] & ~mask):
                avail |= lb
            candidates ^= lb

        if mask == 0:
            # The first order never counts as a changeover; pick the smallest
            # id that attains the overall DP optimum.  Bits are extracted low
            # first, i.e. in ascending id order, so strict '<' keeps the
            # smallest id on ties.
            best = INF
            chosen = -1
            candidates = avail
            while candidates:
                lb = candidates & -candidates
                j = lb.bit_length() - 1
                v = dp[lb * num_families + family[j]]
                if v < best:
                    best = v
                    chosen = j
                candidates ^= lb
            remaining_cost = best
        else:
            chosen = -1
            for j in ids_asc:
                if not (avail & (1 << j)):
                    continue
                edge_cost = int(family[j] != last_f)
                if (
                    dp[(mask | (1 << j)) * num_families + family[j]]
                    + edge_cost
                    == remaining_cost
                ):
                    chosen = j
                    remaining_cost -= edge_cost
                    break
        if chosen < 0:  # pragma: no cover - cannot happen on a DAG
            raise RuntimeError("DP reconstruction failed")
        order.append(chosen)
        mask |= 1 << chosen
        last_f = family[chosen]
    return order


def solve(
    jobs: list[Job],
    edges: list[tuple[str, str]],
) -> dict:
    """Compute the schedule payload.

    Returns either::

        {"status": "CYCLE", "cycle": [...]}

    or::

        {"status": "OK", "order": [...], "changeover_count": k,
         "changeover_positions": [...], "changeovers": [...]}
    """
    cycle = find_cycle(jobs, edges)
    if cycle is not None:
        return {"status": "CYCLE", "cycle": cycle}

    ids = sorted((j.id for j in jobs), key=lambda s: s.encode("utf-8"))
    idx = {jid: i for i, jid in enumerate(ids)}
    n = len(ids)

    family_names = sorted({j.family for j in jobs}, key=lambda s: s.encode("utf-8"))
    fam_idx = {f: i for i, f in enumerate(family_names)}
    family_of_id = {j.id: j.family for j in jobs}
    family = [fam_idx[family_of_id[jid]] for jid in ids]

    prereq = [0] * n
    for before, after in edges:
        prereq[idx[after]] |= 1 << idx[before]

    seq = _optimal_order(n, family, len(family_names), prereq)

    order_ids = [ids[i] for i in seq]
    positions: list[int] = []
    details: list[dict] = []
    prev_f = None
    for pos, jid in enumerate(order_ids, start=1):
        f = family_of_id[jid]
        if prev_f is not None and f != prev_f:
            positions.append(pos)
            details.append(
                {
                    "position": pos,
                    "from": {"id": order_ids[pos - 2], "family": prev_f},
                    "to": {"id": jid, "family": f},
                }
            )
        prev_f = f

    return {
        "status": "OK",
        "order": order_ids,
        "changeover_count": len(positions),
        "changeover_positions": positions,
        "changeovers": details,
    }

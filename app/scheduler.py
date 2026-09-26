"""Exact low-changeover topological scheduler.

Given work orders (id, family), ``before -> after`` precedence edges and
optional *immediate* pairs (``after`` must be scheduled directly after
``before``, with no other order in between), find an ordering that

1. satisfies every precedence edge and every immediate pair,
2. minimises the number of changeovers (positions where adjacent orders have
   different families; the first order never counts),
3. among all optimal orderings, is lexicographically smallest on the order's
   UTF-8 byte sequences.

Immediate pairs are validated upstream so that every order has at most one
immediate predecessor and one immediate successor; the pairs therefore form
vertex-disjoint chains whose internal order is fixed.  Each maximal chain is
condensed into a single *block* and the optimiser runs a subset dynamic
program over blocks::

    dp(mask, f) = minimum remaining changeovers when the blocks in *mask*
                  have already been placed and the family of the last placed
                  order is *f*.

Placing block B after a last-family of f costs
``[first_family(B) != f] + internal_changeovers(B)``, so the fixed internal
order of every chain and the inter-block precedence relations are decided
together in a single optimisation pass -- never by repairing an unconstrained
optimum after the fact.

With at most 18 orders there are at most 2**18 * 18 states, stored as a flat
``bytearray`` (max cost is 17, ``INF = 0x7F``).  The lexicographically
smallest optimum is reconstructed greedily: at every step, try the available
blocks in ascending UTF-8 order of their first order id and pick the first
one that can still attain the DP optimum (distinct blocks start with
distinct ids, so the first id decides the lexicographic comparison).

Feasibility is settled before optimising: a cyclic immediate relation, a
precedence edge that contradicts the fixed order of its own chain, or a
cycle in the condensed block graph makes the instance ``UNSCHEDULABLE``.
A cycle in the raw precedence graph itself is reported first, as ``CYCLE``.
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


def _has_cycle(prereq: list[int]) -> bool:
    """True when the precedence bitmask graph contains a directed cycle."""
    m = len(prereq)
    succ: list[list[int]] = [[] for _ in range(m)]
    for v in range(m):
        bits = prereq[v]
        while bits:
            lb = bits & -bits
            succ[lb.bit_length() - 1].append(v)
            bits ^= lb

    WHITE, GRAY, BLACK = 0, 1, 2
    color = [WHITE] * m

    def dfs(u: int) -> bool:
        color[u] = GRAY
        for w in succ[u]:
            if color[w] == GRAY:
                return True
            if color[w] == WHITE and dfs(w):
                return True
        color[u] = BLACK
        return False

    return any(color[s] == WHITE and dfs(s) for s in range(m))


def _build_blocks(
    n: int,
    edges: list[tuple[int, int]],
    immediate: list[tuple[int, int]],
) -> tuple[list[list[int]], list[int]] | None:
    """Condense maximal immediate chains into blocks.

    Returns ``(blocks, block_prereq)`` where ``blocks[b]`` is the fixed job
    sequence of block *b* and ``block_prereq[b]`` the bitmask of blocks that
    must be placed before *b*.  Returns ``None`` when the immediate
    requirements cannot all be satisfied even though the precedence graph
    itself is acyclic (``UNSCHEDULABLE``).
    """
    succ: dict[int, int] = {}
    pred: dict[int, int] = {}
    for u, v in immediate:
        succ[u] = v
        pred[v] = u

    chained = set(succ) | set(pred)
    blocks: list[list[int]] = []
    covered: set[int] = set()
    for start in sorted(chained):
        if start in pred:
            continue  # not a chain head
        chain = [start]
        covered.add(start)
        while chain[-1] in succ:
            chain.append(succ[chain[-1]])
            covered.add(chain[-1])
        blocks.append(chain)
    if covered != chained:
        return None  # immediate pairs contain a directed cycle

    block_of = [0] * n
    for b, chain in enumerate(blocks):
        for j in chain:
            block_of[j] = b
    for j in range(n):
        if j not in chained:
            block_of[j] = len(blocks)
            blocks.append([j])

    pos_in_block = [0] * n
    for chain in blocks:
        for pos, j in enumerate(chain):
            pos_in_block[j] = pos

    block_prereq = [0] * len(blocks)
    for u, v in edges:
        bu, bv = block_of[u], block_of[v]
        if bu == bv:
            if pos_in_block[u] > pos_in_block[v]:
                return None  # edge contradicts its chain's fixed order
        else:
            block_prereq[bv] |= 1 << bu

    if _has_cycle(block_prereq):
        # Acyclic edges can still be unschedulable once chains are condensed,
        # e.g. a -> x and x -> b while b must directly follow a.
        return None
    return blocks, block_prereq


def _optimal_order(
    blocks: list[list[int]],
    family: list[int],
    num_families: int,
    prereq: list[int],
) -> list[int]:
    """DP over block subsets; returns the sequence of job indices."""
    m = len(blocks)
    first_fam = [family[chain[0]] for chain in blocks]
    last_fam = [family[chain[-1]] for chain in blocks]
    internal = [
        sum(1 for a, b in zip(chain, chain[1:]) if family[a] != family[b])
        for chain in blocks
    ]
    size = 1 << m
    full = size - 1
    # dp[mask * num_families + f]
    dp = bytearray([INF]) * (size * num_families)
    # Placing the first block costs nothing regardless of its family; the
    # full-mask terminal value is 0 for every family (no orders left).
    for f in range(num_families):
        dp[full * num_families + f] = 0

    allbits = full

    for mask in range(full - 1, -1, -1):
        # Blocks that may be placed next: not yet placed, all prerequisites in.
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

        # best_by_fam[g] = min over available blocks B entering with family g
        # of internal(B) + dp(mask|B, last_family(B)).
        best_by_fam = [INF] * num_families
        candidates = avail
        while candidates:
            lb = candidates & -candidates
            j = lb.bit_length() - 1
            nxt = dp[(mask | lb) * num_families + last_fam[j]]
            if nxt < INF:
                v = internal[j] + nxt
                if v < best_by_fam[first_fam[j]]:
                    best_by_fam[first_fam[j]] = v
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
        #   dp(mask, f) = min over available B of
        #       internal(B) + dp(mask|B, last_family(B)) + [first_family(B) != f]
        # = min(best_by_fam[f], min_from_another_family + 1).
        base = mask * num_families
        for f in range(num_families):
            same = best_by_fam[f]
            other = m2 if f == m1_f else m1
            if other == INF:
                # No available block entering with another family.
                dp[base + f] = same
            elif same == INF:
                dp[base + f] = other + 1
            else:
                dp[base + f] = same if same <= other + 1 else other + 1

    # Greedy reconstruction with ascending UTF-8 id order.  Distinct blocks
    # start with distinct order ids, so comparing the first id of each
    # candidate block decides the lexicographic order of the emitted
    # sequences; job indices already ascend in UTF-8 id order.
    blocks_asc = sorted(range(m), key=lambda b: blocks[b][0])
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

        chosen = -1
        if mask == 0:
            # The first block never counts as a boundary changeover; pick the
            # smallest first id that attains the overall DP optimum.
            # Candidates are tried in ascending first-id order, so strict '<'
            # keeps the smallest id on ties.  The block's own internal
            # changeovers are spent immediately.
            best = INF
            for b in blocks_asc:
                if not (avail & (1 << b)):
                    continue
                v = internal[b] + dp[(1 << b) * num_families + last_fam[b]]
                if v < best:
                    best = v
                    chosen = b
            remaining_cost = best - internal[chosen]
        else:
            for b in blocks_asc:
                if not (avail & (1 << b)):
                    continue
                step = int(first_fam[b] != last_f) + internal[b]
                if (
                    step + dp[(mask | (1 << b)) * num_families + last_fam[b]]
                    == remaining_cost
                ):
                    chosen = b
                    remaining_cost -= step
                    break
        if chosen < 0:  # pragma: no cover - cannot happen on a DAG
            raise RuntimeError("DP reconstruction failed")
        order.extend(blocks[chosen])
        mask |= 1 << chosen
        last_f = last_fam[chosen]
    return order


def solve(
    jobs: list[Job],
    edges: list[tuple[str, str]],
    immediate: list[tuple[str, str]] | None = None,
) -> dict:
    """Compute the schedule payload.

    Returns one of::

        {"status": "CYCLE", "cycle": [...]}
        {"status": "UNSCHEDULABLE"}
        {"status": "OK", "order": [...], "changeover_count": k,
         "changeover_positions": [...], "changeovers": [...]}

    ``CYCLE`` (raw precedence graph has a directed cycle) and
    ``UNSCHEDULABLE`` (acyclic, but the immediate pairs cannot all be
    honoured) never carry a partial schedule.
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

    edge_idx = [(idx[before], idx[after]) for before, after in edges]
    imm_idx = [(idx[before], idx[after]) for before, after in (immediate or [])]

    built = _build_blocks(n, edge_idx, imm_idx)
    if built is None:
        return {"status": "UNSCHEDULABLE"}
    blocks, block_prereq = built

    seq = _optimal_order(blocks, family, len(family_names), block_prereq)

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

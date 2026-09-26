"""Immediate (directly-follows) pairs: exhaustive oracle cross-checks,
targeted chain/UNSCHEDULABLE cases, and HTTP-level validation."""

from __future__ import annotations

import itertools
import random

from fastapi.testclient import TestClient

from app.main import app
from app.scheduler import Job, solve

client = TestClient(app)


def brute_force(
    jobs: list[Job],
    edges: list[tuple[str, str]],
    immediate: list[tuple[str, str]],
) -> dict | None:
    """Enumerate every permutation; return the optimum defined by the spec,
    or ``None`` when no permutation satisfies edges + immediate pairs."""
    fam = {j.id: j.family for j in jobs}
    ids = [j.id for j in jobs]
    before_of: dict[str, set[str]] = {jid: set() for jid in ids}
    for u, v in edges:
        before_of[v].add(u)

    best: tuple | None = None
    best_order: list[str] | None = None
    for perm in itertools.permutations(ids):
        placed: set[str] = set()
        ok = True
        for jid in perm:
            if not before_of[jid] <= placed:
                ok = False
                break
            placed.add(jid)
        if not ok:
            continue
        pos = {jid: i for i, jid in enumerate(perm)}
        if any(pos[u] + 1 != pos[v] for u, v in immediate):
            continue
        cost = sum(
            1 for i in range(1, len(perm)) if fam[perm[i]] != fam[perm[i - 1]]
        )
        key = (cost, tuple(x.encode("utf-8") for x in perm))
        if best is None or key < best:
            best = key
            best_order = list(perm)
    if best_order is None:
        return None
    positions = [
        i + 1
        for i in range(1, len(best_order))
        if fam[best_order[i]] != fam[best_order[i - 1]]
    ]
    return {"order": best_order, "count": best[0], "positions": positions}


def random_instance(
    rng: random.Random, n: int
) -> tuple[list[Job], list[tuple[str, str]], list[tuple[str, str]]]:
    ids = [f"w{i:02d}" for i in range(n)]
    families = [rng.choice(["A", "B", "C"]) for _ in range(n)]
    jobs = [Job(id=ids[i], family=families[i]) for i in range(n)]
    edges: set[tuple[str, str]] = set()
    for i in range(n):
        for j in range(i + 1, n):
            if rng.random() < 0.3:
                edges.add((ids[i], ids[j]))
    # Non-forking immediate structure: adjacent pairs of a random permutation
    # form vertex-disjoint chains of random lengths.
    perm = ids[:]
    rng.shuffle(perm)
    immediate = [(perm[k], perm[k + 1]) for k in range(n - 1) if rng.random() < 0.4]
    return jobs, sorted(edges), immediate


def check_against_oracle(
    jobs: list[Job],
    edges: list[tuple[str, str]],
    immediate: list[tuple[str, str]],
) -> str:
    result = solve(jobs, edges, immediate)
    oracle = brute_force(jobs, edges, immediate)
    if oracle is None:
        assert result["status"] == "UNSCHEDULABLE"
        # UNSCHEDULABLE responses must never contain a partial schedule.
        assert "order" not in result
        assert "changeover_count" not in result
        return "UNSCHEDULABLE"
    assert result["status"] == "OK"
    assert result["order"] == oracle["order"]
    assert result["changeover_count"] == oracle["count"]
    assert result["changeover_positions"] == oracle["positions"]
    # Every immediate pair ends up adjacent, in the requested direction.
    pos = {jid: i for i, jid in enumerate(result["order"])}
    for u, v in immediate:
        assert pos[u] + 1 == pos[v]
    return "OK"


def test_exhaustive_random_with_immediate() -> None:
    rng = random.Random(20260926)
    seen: set[str] = set()
    for n in range(2, 8):
        for _ in range(60):
            jobs, edges, immediate = random_instance(rng, n)
            seen.add(check_against_oracle(jobs, edges, immediate))
    # The random mix must actually exercise both outcomes.
    assert seen == {"OK", "UNSCHEDULABLE"}


def test_fixed_cases_with_immediate() -> None:
    # Chain of three forced adjacent; families align -> no changeovers.
    check_against_oracle(
        [Job(id="a", family="X"), Job(id="b", family="X"), Job(id="c", family="X")],
        [],
        [("a", "b"), ("b", "c")],
    )
    # Immediate pair fights family grouping.
    check_against_oracle(
        [
            Job(id="a", family="X"),
            Job(id="b", family="Y"),
            Job(id="c", family="X"),
            Job(id="d", family="Y"),
        ],
        [("a", "c")],
        [("b", "c")],
    )
    # Two chains plus a singleton, all families distinct.
    check_against_oracle(
        [Job(id=str(i), family=f"f{i}") for i in range(5)],
        [("0", "4")],
        [("1", "2"), ("2", "3")],
    )


def test_joint_optimisation_not_post_hoc_repair() -> None:
    # Without the immediate pair the optimum is [a, b, c] (1 changeover).
    # Sliding "a" next to "c" afterwards would give [a, c, b] (2 changeovers);
    # the true constrained optimum schedules b first and keeps 1 changeover.
    jobs = [Job(id="a", family="X"), Job(id="b", family="X"), Job(id="c", family="Y")]
    result = solve(jobs, [], [("a", "c")])
    assert result["status"] == "OK"
    assert result["order"] == ["b", "a", "c"]
    assert result["changeover_count"] == 1
    assert result["changeover_positions"] == [3]
    assert result["changeovers"] == [
        {
            "position": 3,
            "from": {"id": "a", "family": "X"},
            "to": {"id": "c", "family": "Y"},
        }
    ]


def test_inter_chain_precedence() -> None:
    # Two chains linked by an ordinary edge: the block order is forced.
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="X"),
        Job(id="c", family="Y"),
        Job(id="d", family="Y"),
    ]
    result = solve(jobs, [("b", "c")], [("a", "b"), ("c", "d")])
    assert result["status"] == "OK"
    assert result["order"] == ["a", "b", "c", "d"]
    assert result["changeover_count"] == 1
    assert result["changeover_positions"] == [3]


def test_inter_chain_edge_can_flip_block_order() -> None:
    # Chain [c, d] must finish before chain [a, b] starts (edge d -> a).
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="X"),
        Job(id="c", family="Y"),
        Job(id="d", family="Y"),
    ]
    result = solve(jobs, [("d", "a")], [("a", "b"), ("c", "d")])
    assert result["status"] == "OK"
    assert result["order"] == ["c", "d", "a", "b"]
    assert result["changeover_count"] == 1
    assert result["changeover_positions"] == [3]


def test_lex_tie_compares_chain_heads() -> None:
    # Feasible optima: [c, a, b] and [b, c, a].  The chain's *first* id ("c")
    # decides the comparison, not its smallest member ("a"), so the singleton
    # "b" goes first.
    jobs = [Job(id="a", family="X"), Job(id="b", family="X"), Job(id="c", family="X")]
    result = solve(jobs, [], [("c", "a")])
    assert result["status"] == "OK"
    assert result["order"] == ["b", "c", "a"]
    assert result["changeover_count"] == 0


def test_lex_tie_inside_optimal_block_order() -> None:
    # All same family; several block interleavings are optimal at 0
    # changeovers; the lexicographically smallest emission wins step by step.
    jobs = [Job(id=str(i), family="X") for i in range(5)]
    result = solve(jobs, [], [("1", "2"), ("3", "4")])
    assert result["status"] == "OK"
    assert result["order"] == ["0", "1", "2", "3", "4"]
    assert result["changeover_count"] == 0


def test_internal_chain_changeover_is_counted() -> None:
    jobs = [Job(id="a", family="X"), Job(id="b", family="Y")]
    result = solve(jobs, [], [("a", "b")])
    assert result["status"] == "OK"
    assert result["order"] == ["a", "b"]
    assert result["changeover_count"] == 1
    assert result["changeover_positions"] == [2]
    assert result["changeovers"] == [
        {
            "position": 2,
            "from": {"id": "a", "family": "X"},
            "to": {"id": "b", "family": "Y"},
        }
    ]


def test_unschedulable_edge_contradicts_chain_order() -> None:
    # b must directly precede a, but the edge demands a before b.
    jobs = [Job(id="a", family="X"), Job(id="b", family="X")]
    result = solve(jobs, [("a", "b")], [("b", "a")])
    assert result == {"status": "UNSCHEDULABLE"}


def test_unschedulable_acyclic_but_no_room_for_adjacency() -> None:
    # The precedence graph a -> x -> b is acyclic, yet b must directly
    # follow a, leaving no slot for x.
    jobs = [Job(id="a", family="X"), Job(id="b", family="X"), Job(id="x", family="X")]
    result = solve(jobs, [("a", "x"), ("x", "b")], [("a", "b")])
    assert result == {"status": "UNSCHEDULABLE"}


def test_unschedulable_immediate_cycle() -> None:
    jobs = [Job(id="a", family="X"), Job(id="b", family="X"), Job(id="c", family="X")]
    result = solve(jobs, [], [("a", "b"), ("b", "c"), ("c", "a")])
    assert result == {"status": "UNSCHEDULABLE"}


def test_unschedulable_two_node_immediate_cycle() -> None:
    jobs = [Job(id="a", family="X"), Job(id="b", family="X")]
    result = solve(jobs, [], [("a", "b"), ("b", "a")])
    assert result == {"status": "UNSCHEDULABLE"}


def test_unschedulable_has_no_partial_schedule() -> None:
    # Acyclic edges, but c must sit directly after a while the edges force
    # b in between: no complete order can honour everything.
    jobs = [Job(id="a", family="X"), Job(id="b", family="Y"), Job(id="c", family="Z")]
    result = solve(jobs, [("a", "b"), ("b", "c")], [("a", "c")])
    assert result["status"] == "UNSCHEDULABLE"
    assert set(result) == {"status"}


def test_edge_cycle_still_reports_cycle_with_immediate() -> None:
    # CYCLE takes precedence over UNSCHEDULABLE and carries no schedule.
    jobs = [Job(id="a", family="X"), Job(id="b", family="Y"), Job(id="c", family="Z")]
    result = solve(jobs, [("a", "b"), ("b", "c"), ("c", "a")], [("a", "b")])
    assert result["status"] == "CYCLE"
    assert result["cycle"] == ["a", "b", "c"]
    assert set(result) == {"status", "cycle"}


def test_omitted_and_empty_immediate_are_identical() -> None:
    jobs = [
        Job(id="a", family="X"),
        Job(id="b", family="Y"),
        Job(id="c", family="X"),
        Job(id="d", family="Y"),
    ]
    edges = [("b", "a"), ("a", "d")]
    assert solve(jobs, edges) == solve(jobs, edges, None) == solve(jobs, edges, [])


# --- HTTP level -----------------------------------------------------------


def _expect_422(body: object) -> None:
    resp = client.post("/schedule", json=body)
    assert resp.status_code == 422, resp.text


def test_api_immediate_success() -> None:
    body = {
        "jobs": [
            {"id": "a", "family": "X"},
            {"id": "b", "family": "X"},
            {"id": "c", "family": "Y"},
        ],
        "immediate": [{"before": "a", "after": "c"}],
    }
    resp = client.post("/schedule", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "OK"
    assert data["order"] == ["b", "a", "c"]
    assert data["changeover_count"] == 1
    assert data["changeover_positions"] == [3]


def test_api_unschedulable_response_has_no_partial_schedule() -> None:
    body = {
        "jobs": [
            {"id": "a", "family": "X"},
            {"id": "b", "family": "X"},
            {"id": "x", "family": "X"},
        ],
        "edges": [
            {"before": "a", "after": "x"},
            {"before": "x", "after": "b"},
        ],
        "immediate": [{"before": "a", "after": "b"}],
    }
    resp = client.post("/schedule", json=body)
    assert resp.status_code == 200
    assert resp.json() == {"status": "UNSCHEDULABLE"}


def test_api_rejects_immediate_unknown_reference() -> None:
    _expect_422(
        {
            "jobs": [{"id": "a", "family": "X"}, {"id": "b", "family": "Y"}],
            "immediate": [{"before": "a", "after": "zzz"}],
        }
    )


def test_api_rejects_immediate_self_pair() -> None:
    _expect_422(
        {
            "jobs": [{"id": "a", "family": "X"}, {"id": "b", "family": "Y"}],
            "immediate": [{"before": "a", "after": "a"}],
        }
    )


def test_api_rejects_duplicate_immediate_pair() -> None:
    _expect_422(
        {
            "jobs": [{"id": "a", "family": "X"}, {"id": "b", "family": "Y"}],
            "immediate": [
                {"before": "a", "after": "b"},
                {"before": "a", "after": "b"},
            ],
        }
    )


def test_api_rejects_forking_immediate_successor() -> None:
    _expect_422(
        {
            "jobs": [
                {"id": "a", "family": "X"},
                {"id": "b", "family": "Y"},
                {"id": "c", "family": "Z"},
            ],
            "immediate": [
                {"before": "a", "after": "b"},
                {"before": "a", "after": "c"},
            ],
        }
    )


def test_api_rejects_forking_immediate_predecessor() -> None:
    _expect_422(
        {
            "jobs": [
                {"id": "a", "family": "X"},
                {"id": "b", "family": "Y"},
                {"id": "c", "family": "Z"},
            ],
            "immediate": [
                {"before": "a", "after": "c"},
                {"before": "b", "after": "c"},
            ],
        }
    )


def test_api_rejects_immediate_extra_fields_and_bad_types() -> None:
    _expect_422(
        {
            "jobs": [{"id": "a", "family": "X"}, {"id": "b", "family": "Y"}],
            "immediate": [{"before": "a", "after": "b", "weight": 1}],
        }
    )
    _expect_422(
        {
            "jobs": [{"id": "a", "family": "X"}, {"id": "b", "family": "Y"}],
            "immediate": [{"before": "a"}],
        }
    )
    _expect_422(
        {
            "jobs": [{"id": "a", "family": "X"}, {"id": "b", "family": "Y"}],
            "immediate": {"before": "a", "after": "b"},
        }
    )


def test_api_omitted_immediate_is_unchanged() -> None:
    # Old request bodies (no ``immediate`` key) get the old responses.
    body = {
        "jobs": [
            {"id": "a", "family": "X"},
            {"id": "b", "family": "X"},
            {"id": "c", "family": "Y"},
        ],
        "edges": [{"before": "a", "after": "b"}],
    }
    resp = client.post("/schedule", json=body)
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "OK",
        "order": ["a", "b", "c"],
        "changeover_count": 1,
        "changeover_positions": [3],
        "changeovers": [
            {
                "position": 3,
                "from": {"id": "b", "family": "X"},
                "to": {"id": "c", "family": "Y"},
            }
        ],
    }

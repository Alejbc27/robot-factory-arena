"""Tournament driver: the user-facing seam over the graph topology (§8/§10).

``run_tournament`` prefers the compiled LangGraph topology when the optional
extra is installed and otherwise falls back to :func:`drive`, a stdlib-only
walker over the SAME node and route functions with the SAME edges. Both paths
execute the identical deterministic tournament, and the acceptance tests hold
their leaderboards byte-identical across the two paths. ``drive`` mirrors
``build_graph``'s edge topology one-to-one; the route helpers in
:mod:`harness.graph` stay the single source of topology truth. There is no
third path and no silent behavioral difference between the two.
"""

from . import graph, state

# One full tournament needs far more super-steps than the langgraph default.
_RECURSION_LIMIT = 1000


def drive(state_value, deps):
    """Executes exactly the build_graph() edge topology with pure functions."""
    state_value = graph.node_freeze_tournament_config(state_value, deps)
    state_value = graph.node_assign_work(state_value, deps)
    state_value = graph.node_plan_context(state_value, deps)
    for _ in range(200):
        plan_route = graph.route_after_plan(state_value, deps)
        if plan_route == "approve_r1":
            state_value = graph.node_approve_r1(state_value, deps)
            approval_route = graph.route_after_approval(state_value)
            if approval_route == "approve_r1":
                continue
            if approval_route == "retry_or_finish":
                state_value = graph.node_retry_or_finish(state_value, deps)
            else:
                state_value = graph.node_merge_and_freeze_context(state_value, deps)
                state_value = graph.node_register_launch(state_value, deps)
                state_value = graph.node_launch_subprocess(state_value, deps)
                state_value = graph.node_collect_and_bind_evidence(state_value, deps)
                state_value = graph.node_retry_or_finish(state_value, deps)
        elif plan_route == "collect_and_bind_evidence":
            state_value = graph.node_collect_and_bind_evidence(state_value, deps)
            state_value = graph.node_retry_or_finish(state_value, deps)
        elif plan_route == "retry_or_finish":
            state_value = graph.node_retry_or_finish(state_value, deps)
        elif plan_route == "barrier_merge_r3":
            state_value = graph.node_barrier_merge_r3(state_value, deps)
            barrier_route = graph.route_after_barrier(state_value)
            if barrier_route == "collect_and_bind_evidence":
                # barrier drain: resolve the pending prior-round launch first
                state_value = graph.node_collect_and_bind_evidence(state_value, deps)
            elif barrier_route == "plan_context":
                # barrier drain: a branch missing its prior-round work is
                # routed through its own plan/launch flow (conditional edge)
                state_value = graph.node_plan_context(state_value, deps)
                continue
            else:
                state_value = graph.node_register_launch(state_value, deps)
                state_value = graph.node_launch_subprocess(state_value, deps)
                state_value = graph.node_collect_and_bind_evidence(state_value, deps)
            state_value = graph.node_retry_or_finish(state_value, deps)
        else:
            state_value = graph.node_merge_and_freeze_context(state_value, deps)
            state_value = graph.node_register_launch(state_value, deps)
            state_value = graph.node_launch_subprocess(state_value, deps)
            state_value = graph.node_collect_and_bind_evidence(state_value, deps)
            state_value = graph.node_retry_or_finish(state_value, deps)
        route = graph.route_after_retry(state_value)
        if route == "aggregate_leaderboard":
            return graph.node_aggregate_leaderboard(state_value, deps)
        if route == "plan_context":
            state_value = graph.node_plan_context(state_value, deps)
    raise AssertionError("topology step budget exceeded")


def run_tournament(deps):
    """Drive one tournament to completion: the compiled LangGraph topology when
    the optional extra is installed, else the identical stdlib walker."""
    try:
        compiled = graph.build_graph(deps)
    except graph.HarnessGraphError:
        return drive(state.initial_state(), deps)
    return compiled.invoke(
        state.initial_state(), config={"recursion_limit": _RECURSION_LIMIT}
    )

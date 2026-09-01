"""bounded-v0: deterministic, pure, cost-minimizing reference policy (REQ-01).

Choose the pending order minimizing ``Manhattan(robot, shelf)
+ Manhattan(shelf, base) + 2``, tie by order id; then follow the same
pick/place routing as greedy-v0 (base navigation when carrying; N, E, S, W
tie order on shortest moves). The module reads only the serialized
observation and imports only the contract boundary — no runtime, filesystem,
randomness, or verifier code. Routing helpers are intentionally duplicated
from greedy.py because the B1 declared layout has no shared policy helper
module; the two reference policies stay self-contained by design.
"""
from robot_factory_arena.contracts.policy import policy_manifest_digest
from robot_factory_arena.contracts.tool_call import ToolCall

POLICY_ID = "bounded-v0"

INTENT_GOTO_BASE = "goto-base"

# Cardinal probe order required by REQ-01: north, east, south, west.
_DIRECTIONS = (
    ("north", (0, 1)),
    ("east", (1, 0)),
    ("south", (0, -1)),
    ("west", (-1, 0)),
)
_GRID = 10


def _manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _step_toward(position, target):
    """First in-grid cardinal step that reduces Manhattan distance (N,E,S,W)."""
    distance = _manhattan(position, target)
    if distance == 0:
        raise ValueError("already at target")
    x, y = position
    for name, (dx, dy) in _DIRECTIONS:
        candidate = (x + dx, y + dy)
        if (
            0 <= candidate[0] < _GRID
            and 0 <= candidate[1] < _GRID
            and _manhattan(candidate, target) < distance
        ):
            return name
    raise ValueError("no distance-reducing cardinal move available")


def _pending(world):
    completed = set(world["completed_orders"])
    return [
        order for order in world["orders"] if order["order_id"] not in completed
    ]


def _source_shelf(world, item_id):
    for shelf in world["shelves"]:
        if shelf["item_id"] == item_id:
            return shelf
    return None


class BoundedPolicy:
    """Manhattan-cost-minimizing baseline; one ToolCall per decision."""

    policy_id = POLICY_ID
    policy_digest = policy_manifest_digest(POLICY_ID)

    def decide(self, observation) -> ToolCall:
        world = observation["world_state"]
        robot = world["robot"]
        position = tuple(robot["position"])
        base = tuple(robot["base_position"])
        carrying = robot["inventory_item_id"]
        logical_run_id = observation["logical_run_id"]
        policy_step = observation["policy_steps"]["used"] + 1
        pending = _pending(world)

        def emit(tool, arguments, intent):
            key = f"op/{logical_run_id}/step-{policy_step}/intent-{intent}"
            return ToolCall(1, tool, arguments, key)

        if carrying is not None:
            matching = [order for order in pending if order["item_id"] == carrying]
            if matching:
                target = matching[0]
            elif pending:
                # Deterministic fallback: rejected as order_item_mismatch.
                target = pending[0]
            else:
                target = world["orders"][0]
            if position == base:
                return emit(
                    "place",
                    {"order_id": target["order_id"]},
                    f"place-{target['order_id']}",
                )
            return emit("move", {"direction": _step_toward(position, base)}, INTENT_GOTO_BASE)

        if not pending:
            if position != base:
                return emit("move", {"direction": _step_toward(position, base)}, INTENT_GOTO_BASE)
            target = world["orders"][0]
            return emit(
                "place",
                {"order_id": target["order_id"]},
                f"place-{target['order_id']}",
            )

        def cost(order):
            shelf = _source_shelf(world, order["item_id"])
            if shelf is None:
                raise ValueError(
                    f"observation inconsistent: no source shelf for {order['item_id']}"
                )
            shelf_position = tuple(shelf["position"])
            return (
                _manhattan(position, shelf_position)
                + _manhattan(shelf_position, base)
                + 2,
                order["order_id"],
            )

        order = min(pending, key=cost)
        shelf = _source_shelf(world, order["item_id"])
        if shelf is None:
            raise ValueError(
                f"observation inconsistent: no source shelf for {order['item_id']}"
            )
        shelf_position = tuple(shelf["position"])
        if position == shelf_position:
            return emit(
                "pick",
                {"shelf_id": shelf["shelf_id"], "item_id": order["item_id"]},
                f"pick-{shelf['shelf_id']}",
            )
        return emit(
            "move",
            {"direction": _step_toward(position, shelf_position)},
            f"goto-{shelf['shelf_id']}",
        )

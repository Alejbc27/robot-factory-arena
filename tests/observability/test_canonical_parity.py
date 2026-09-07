"""Deep canonical parity: observability must accept/reject exactly like the core.

The observability runtime stays isolated (no frozen imports); these parity
tests MAY import the core authority to prove, over one shared value table,
that ``observability.canonical_json_bytes`` accepts exactly the same values,
raises on exactly the same rejects, and produces byte-identical output
(PR #29 review blocker 3: floats inside tuples, non-string keys, and
unknown types must fail closed, not serialize).
"""

import unittest

from observability import ArtifactError
from observability import canonical_json_bytes as mirror_bytes
from robot_factory_arena.evidence.canonical import (
  canonical_json_bytes as core_bytes,
)


class _Opaque:
  """A type canonical JSON can never represent."""


ACCEPT = [
  ("null", None),
  ("true", True),
  ("false", False),
  ("int", 17),
  ("negative_int", -4),
  ("zero", 0),
  ("empty_string", ""),
  ("unicode_string", "éü✓"),
  ("dict_sorted", {"b": 1, "a": None, "c": [2, 1]}),
  ("nested_containers", {"x": [1, (2, {"y": (None, True)})]}),
  ("tuple_top_level", (1, [2, (3,)])),
  ("empty_containers", ({}, [], ())),
]

REJECT = [
  ("float", 0.5),
  ("nan", float("nan")),
  ("inf", float("inf")),
  ("float_in_tuple", (1, (0.5,))),
  ("float_in_nested_list", [1, {"k": [0.25]}]),
  ("int_key", {1: "a"}),
  ("bool_key", {True: 1}),
  ("none_key", {None: 1}),
  ("tuple_key", {(1, 2): "k"}),
  ("nested_int_key", {"outer": {2: "v"}}),
  ("set_value", {"s": {1, 2}}),
  ("opaque_object", _Opaque()),
]


def _attempt(serialize, value):
  try:
    return serialize(value), None
  except Exception as exc:
    return None, exc


class TestCanonicalParityWithCore(unittest.TestCase):
  def test_accepted_values_and_bytes_are_identical(self):
    for label, value in ACCEPT:
      with self.subTest(value=label):
        core, core_error = _attempt(core_bytes, value)
        mirror, mirror_error = _attempt(mirror_bytes, value)
        self.assertIsNone(core_error, label)
        self.assertIsNone(mirror_error, label)
        self.assertEqual(core, mirror, label)

  def test_rejected_values_are_identical(self):
    for label, value in REJECT:
      with self.subTest(value=label):
        core, core_error = _attempt(core_bytes, value)
        mirror, mirror_error = _attempt(mirror_bytes, value)
        self.assertIsNone(core, label)
        self.assertIsNone(mirror, label)
        self.assertIsNotNone(core_error, label)
        if not isinstance(mirror_error, ArtifactError):
          self.fail(f"{label}: mirror must raise ArtifactError")
        self.assertEqual(mirror_error.reason, "non_canonical_json", label)


if __name__ == "__main__":
  unittest.main()

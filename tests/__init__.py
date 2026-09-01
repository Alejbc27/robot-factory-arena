"""Test package marker for robot-factory-arena v0 (REQ-08).

Single gate command (run from the repository root):
    python3 -m unittest discover -s tests -v

Note: discovery imports the eight test modules as top-level modules under the
locked command, so test modules must not rely on the "tests." package prefix;
each module stays self-contained.
"""

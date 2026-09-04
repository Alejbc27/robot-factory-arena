"""Isolated tournament harness (Phase 6 foundation).

Orchestrates the robot_factory_arena CLI as a subprocess over sealed JSON
artifacts and never imports the core package. LangGraph is an optional extra
(`harness` extra in pyproject.toml); this foundation is stdlib-only and the
graph wiring lands in Unit 3+.
"""

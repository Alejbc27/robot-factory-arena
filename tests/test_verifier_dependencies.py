"""Verifier dependency denylist tests (REQ-06, REQ-08).

Populated in B5 (T-18, T-21).
"""
import unittest


class TestModuleDiscovered(unittest.TestCase):
    """T-01 placeholder: keeps discovery green until real tests land."""

    def test_module_is_discovered(self):
        # Identity-neutral: the module only has to be discovered and loaded.
        self.assertIsNotNone(__doc__)

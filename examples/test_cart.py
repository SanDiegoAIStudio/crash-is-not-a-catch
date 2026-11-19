import unittest

from cart import fmt, total


class Weak(unittest.TestCase):
    def test_total_formats(self):
        self.assertEqual(fmt(total([1, 2])), "$3.00")


class Strong(unittest.TestCase):
    def test_total_formats(self):
        self.assertEqual(total([1, 2]), 3)
        self.assertEqual(fmt(total([1, 2])), "$3.00")

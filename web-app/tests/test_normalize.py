from decimal import Decimal
import unittest

from src.normalize import normalize_weight_kg


class WeightNormalizationTests(unittest.TestCase):
    def test_bare_number_is_assumed_to_be_kg(self) -> None:
        self.assertEqual(normalize_weight_kg("14500"), Decimal("14500"))

    def test_unit_and_thousands_separator_are_supported(self) -> None:
        self.assertEqual(normalize_weight_kg("14,500 KG"), Decimal("14500"))

    def test_invalid_weight_is_rejected(self) -> None:
        self.assertIsNone(normalize_weight_kg("not a weight"))


if __name__ == "__main__":
    unittest.main()

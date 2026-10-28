import unittest

from library_v2.ingredient_identity import (ingredient_match_spans, ingredient_occurrence_matches,
                                          normalize_with_offsets)
from library_v2.store import ingredient_text, ingredient_matches


class ScopedIngredientIdentityTest(unittest.TestCase):
    def test_wrapped_derivative_does_not_count_as_fruit(self):
        source = "2 tablespoons apple\ncider vinegar"
        split = source.index("\n")
        self.assertFalse(ingredient_occurrence_matches("apple", source, 0, split))
        self.assertFalse(ingredient_occurrence_matches("apple cider", source, 0, split))
        self.assertTrue(ingredient_occurrence_matches("apple cider vinegar", source, 0, split))
        self.assertTrue(ingredient_occurrence_matches("apple cider vinegar", source, split + 1, len(source)))
        self.assertFalse(ingredient_occurrence_matches("apple cider vinegar", source, 0, split, "required_only"))
        self.assertTrue(ingredient_occurrence_matches("apple cider vinegar", source, 0, len(source), "required_only"))

    def test_optional_fruit_elsewhere_does_not_validate_derivative_row(self):
        source = "2 tablespoons apple\ncider vinegar\n1 apple (optional)"
        first_end = source.index("\n")
        fruit_start = source.index("1 apple")
        self.assertFalse(ingredient_occurrence_matches("apple", source, 0, first_end, "required_only"))
        self.assertFalse(ingredient_occurrence_matches("apple", source, 0, first_end))
        self.assertTrue(ingredient_occurrence_matches("apple", source, fruit_start, len(source)))

    def test_separate_quantified_rows_are_not_assembled_into_a_compound(self):
        source = "1 apple\n2 tablespoons cider vinegar"
        apple_end = source.index("\n")
        self.assertTrue(ingredient_occurrence_matches("apple", source, 0, apple_end, "required_only"))
        self.assertFalse(ingredient_occurrence_matches("apple cider vinegar", source, 0, apple_end))
        # Source blocks are independent boundaries, even without quantities.
        self.assertTrue(ingredient_occurrence_matches("apple", "apple", 0, 5))
        self.assertFalse(ingredient_occurrence_matches("apple", "cider vinegar", 0, 13))

    def test_other_wrapped_derivatives_and_homonyms_use_existing_policy(self):
        fixtures = [("fish", "1 spoon fish\nsauce"), ("chicken", "2 cups chicken\nstock"),
                    ("oyster", "1 pound oyster\nmushrooms"), ("beef", "2 beef\ntomatoes"),
                    ("chocolate", "2 chocolate\nclams"), ("pork", "2 slices turkey\nbacon"),
                    ("fennel", "1 teaspoon fennel\nseeds"), ("cocoa", "1 spoon cocoa\nbutter")]
        for term, source in fixtures:
            with self.subTest(term=term):
                self.assertFalse(ingredient_occurrence_matches(term, source, 0, source.index("\n")))
        self.assertTrue(ingredient_occurrence_matches("mushroom", fixtures[2][1], fixtures[2][1].index("\n") + 1, len(fixtures[2][1])))

    def test_required_compound_does_not_borrow_optional_suffix_scope(self):
        source = "2 cups chicken\nstock (optional)"
        split = source.index("\n")
        self.assertTrue(ingredient_occurrence_matches("chicken stock", source, 0, split))
        self.assertFalse(ingredient_occurrence_matches("chicken stock", source, 0, split, "required_only"))
        self.assertFalse(ingredient_occurrence_matches("chicken", source, 0, split, "required_only"))

    def test_full_source_adaptation_preserves_bounded_policy(self):
        terms = ("apple", "apple cider", "apple cider vinegar", "fish", "fish stock", "beef",
                 "beef stock", "chicken", "chicken stock", "pork", "oyster", "mushroom",
                 "chocolate", "fennel", "fennel seed", "cocoa", "group:seafood", "group:beef")
        sources = ("apple\ncider vinegar", "1 apple\n2 tablespoons cider vinegar", "beef or chicken\nstock",
                   "vegan beef or chicken stock", "vegetarian chicken\nstock", "fish\nsauce",
                   "oyster\nmushrooms", "beef\ntomatoes", "chocolate\nclams", "turkey\nbacon",
                   "1 teaspoon fennel\nseeds", "1 spoon cocoa\nbutter", "1 pork ribeye steak",
                   "1 sirloin steak\n1 optional apple")
        for source in sources:
            for term in terms:
                with self.subTest(term=term, source=source):
                    self.assertEqual(ingredient_occurrence_matches(term, source, 0, len(source)),
                                     ingredient_matches(term, [source]))

    def test_normalization_keeps_unicode_source_coordinates(self):
        source = "  ﬂour\t\ncrème—fraîche\u00a0ß  "
        normalized, starts, ends = normalize_with_offsets(source)
        self.assertEqual(normalized, ingredient_text(source))
        self.assertEqual(len(normalized), len(starts))
        self.assertEqual(len(normalized), len(ends))
        source = "é½\u00a0APPLE\nCIDER VINEGAR; 1 àpple"
        last = source.index("àpple")
        spans = ingredient_match_spans("apple", source)
        self.assertEqual([(s.start, s.end) for s in spans], [(last, last + len("àpple"))])
        self.assertTrue(ingredient_occurrence_matches("apple", source, last, len(source), "required_only"))

    def test_whitespace_intersection_is_not_food_evidence(self):
        source = "apple\n\t cider vinegar"
        self.assertFalse(ingredient_occurrence_matches("apple cider vinegar", source, 5, 8))

    def test_invalid_ranges_and_modes_fail(self):
        for start, end in [(-1, 2), (0, 0), (0, 6), (True, 4)]:
            with self.assertRaises(ValueError):
                ingredient_occurrence_matches("apple", "apple", start, end)
        with self.assertRaises(ValueError):
            ingredient_occurrence_matches("apple", "apple", 0, 5, "invented")


if __name__ == "__main__":
    unittest.main()

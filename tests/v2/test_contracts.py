import copy
import json
import unittest
from pathlib import Path
from jsonschema.exceptions import ValidationError
from library_v2.contracts import validate_content, validate_input, validate_schemas, revision


class ContractTests(unittest.TestCase):
    def test_draft_schemas_and_canonical_examples(self):
        self.assertEqual(len(validate_schemas()), 7)
        path = Path(__file__).resolve().parents[2] / "evals/library-v2/foundations/contracts/examples.json"
        for record in json.loads(path.read_text())["canonical"]:
            validate_content(record)

    def test_input_constraints_and_no_extra_properties(self):
        validate_input("get_recipes", {"recipe_ids": ["abc"]})
        for args in ({"recipe_ids": []}, {"recipe_ids": ["x"] * 21},
                     {"recipe_ids": ["x"], "write": True}):
            with self.assertRaises(ValidationError):
                validate_input("get_recipes", args)

    def test_revision_stable_keys_sensitive_order(self):
        self.assertEqual(revision({"a": 1, "b": 2}), revision({"b": 2, "revision": "ignored", "a": 1}))
        self.assertNotEqual(revision({"steps": ["first", "second"]}), revision({"steps": ["second", "first"]}))
        with self.assertRaises(ValueError):
            revision({"quantity": float("nan")})


if __name__ == "__main__":
    unittest.main()

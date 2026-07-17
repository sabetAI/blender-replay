import json
import unittest

from core import (
    canonical_json,
    new_session,
    operator_identifier_to_path,
    stable_digest,
    unseen_items,
    validate_session,
)


class CoreTests(unittest.TestCase):
    def test_operator_identifier_supports_rna_and_python_forms(self):
        self.assertEqual(operator_identifier_to_path("MESH_OT_subdivide"), ("mesh", "subdivide"))
        self.assertEqual(operator_identifier_to_path("mesh.subdivide"), ("mesh", "subdivide"))

    def test_operator_identifier_rejects_unknown_form(self):
        with self.assertRaisesRegex(ValueError, "Unrecognised"):
            operator_identifier_to_path("subdivide")

    def test_session_is_json_safe_and_valid(self):
        session = new_session(" Demo ", "5.2.0", "Scene", 250_000)
        self.assertEqual(session["name"], "Demo")
        self.assertEqual(validate_session(json.loads(json.dumps(session))), session)

    def test_validate_session_rejects_future_schema(self):
        with self.assertRaisesRegex(ValueError, "Unsupported schema"):
            validate_session({"schema_version": 99, "events": []})

    def test_digest_is_stable_across_dict_order(self):
        first = {"b": [2, 3], "a": 1}
        second = {"a": 1, "b": [2, 3]}
        self.assertEqual(canonical_json(first), canonical_json(second))
        self.assertEqual(stable_digest(first), stable_digest(second))

    def test_unseen_items_detects_new_and_adjusted_operators(self):
        new, mutated = unseen_items(
            [(1, "same"), (2, "new"), (3, "changed")],
            {1: "same", 3: "old"},
        )
        self.assertEqual(new, [(2, "new")])
        self.assertEqual(mutated, [(3, "changed")])


if __name__ == "__main__":
    unittest.main()

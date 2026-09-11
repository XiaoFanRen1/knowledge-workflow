import unittest
from knowledge_workflow.retrieval import identifiers, body_identifier_coverage


class Identifiers(unittest.TestCase):
    def test_identifiers_require_body_support_and_token_boundaries(self):
        required = identifiers("Does ZX42 ACK 202 mean the device is ready?")
        self.assertEqual(set(required), {"zx42", "ack", "202"})
        self.assertEqual(body_identifier_coverage(required, "The ACK arrives, then follow the link."), 1)
        self.assertEqual(body_identifier_coverage(required, "ZX42 ACK 202 acknowledges transport only."), 3)
        self.assertEqual(body_identifier_coverage(required, "ZX420 ACKNOWLEDGED 2020"), 0)

    def test_normal_prose_is_not_an_identifier_requirement(self):
        self.assertEqual(identifiers("When may the sensor clear the sample ring?"), [])
        self.assertIn("sample_ring", identifiers("What does `sample_ring` contain?"))

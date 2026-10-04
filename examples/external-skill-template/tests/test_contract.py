import sys
import unittest
from pathlib import Path

TEMPLATE_ROOT = Path(__file__).resolve().parents[1]
if str(TEMPLATE_ROOT) not in sys.path:
    sys.path.insert(0, str(TEMPLATE_ROOT))

from gamerhq_skill_example import create_skill
from skill_runtime import validate_skill_factory


class PackageContractTests(unittest.TestCase):
    def test_skill_conforms_to_sdk_contract(self):
        report = validate_skill_factory(
            create_skill,
            expected_skill_id="example-skill",
        )
        self.assertEqual(report.skill_id, "example-skill")


if __name__ == "__main__":
    unittest.main()

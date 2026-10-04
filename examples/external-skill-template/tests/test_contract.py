import unittest

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

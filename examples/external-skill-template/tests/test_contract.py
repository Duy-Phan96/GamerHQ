import sys
import unittest
from pathlib import Path

TEMPLATE_ROOT = Path(__file__).resolve().parents[1]
if str(TEMPLATE_ROOT) not in sys.path:
    sys.path.insert(0, str(TEMPLATE_ROOT))

from gamerhq_skill_example import create_skill
from skill_runtime import require_clean_skill_source, validate_skill_factory, validate_skill_package_metadata


class PackageContractTests(unittest.TestCase):
    def test_package_metadata_matches_skill_identity(self):
        report = validate_skill_package_metadata(TEMPLATE_ROOT / "pyproject.toml")
        self.assertEqual(report.skill_id, "example-skill")
        self.assertEqual(report.runtime_api_version, "1")

    def test_skill_source_stays_portable(self):
        require_clean_skill_source((TEMPLATE_ROOT / "gamerhq_skill_example",))

    def test_skill_conforms_to_sdk_contract(self):
        report = validate_skill_factory(
            create_skill,
            expected_skill_id="example-skill",
        )
        self.assertEqual(report.skill_id, "example-skill")


if __name__ == "__main__":
    unittest.main()

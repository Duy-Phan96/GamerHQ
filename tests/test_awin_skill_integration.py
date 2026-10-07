import importlib.metadata
import inspect
import unittest
from pathlib import Path

from cogs import awin_management
from cogs import server_management
from hosts.gamerhq.skill_packages import BUNDLED_SKILL_IDS
from skill_runtime import validate_skill_implementation
from skill_runtime.runtime.packages import discover_installed_skills
from tools.release_preflight import validate_skill_lock


ROOT = Path(__file__).resolve().parents[1]
AWIN_SHA = "db6bd7ab9500930e1c65aea594be63771e95f4a7"


class AwinSkillIntegrationTests(unittest.TestCase):
    def test_reviewed_awin_distribution_is_pinned_and_discoverable(self):
        lock = (ROOT / "requirements-skills.lock").read_text(encoding="utf-8")
        self.assertEqual(validate_skill_lock(lock), ())
        self.assertIn(
            f"gamerhq-skill-awin-affiliate/archive/{AWIN_SHA}.zip",
            lock,
        )
        self.assertIn("awin-affiliate", BUNDLED_SKILL_IDS)

        distribution = importlib.metadata.distribution("gamerhq-skill-awin-affiliate")
        self.assertEqual(distribution.version, "0.9.0")

        loaded = discover_installed_skills(("awin-affiliate",))
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].distribution, "gamerhq-skill-awin-affiliate")
        report = validate_skill_implementation(loaded[0].skill)
        self.assertEqual(report.skill_id, "awin-affiliate")
        self.assertEqual(report.version, "0.9.0")

    def test_server_management_routes_awin_without_importing_skill_implementation(self):
        source = inspect.getsource(server_management.SkillDetailsView)
        self.assertIn("'awin-affiliate'", source)
        self.assertIn("from cogs.awin_management import open_awin", source)

        awin_source = inspect.getsource(awin_management)
        self.assertNotIn("gamerhq_skill_awin_affiliate", awin_source)
        self.assertIn("awin-affiliate.diagnostics.v1", awin_source)
        self.assertIn("awin-affiliate.setup.connect.v1", awin_source)
        self.assertIn("awin-affiliate.creatives.list.v1", awin_source)

    def test_overview_uses_safe_diagnostics_only(self):
        diagnostics = {
            "setup": {
                "connected": True,
                "publisherSelected": True,
                "publisher": {"id": "20", "name": "GamerHQ"},
                "token": "must-not-render",
                "secret": "must-not-render-either",
            },
            "creatives": {
                "total": 3,
                "enabled": 2,
                "disabled": 1,
                "byState": {"ACTIVE": 1, "NEW": 1, "MISSING": 1},
            },
            "campaigns": {"active": 1, "paused": 2, "blocked": 3},
            "deliveryHistory": {"sent": 4, "blocked": 5, "failed": 6},
        }

        text = awin_management._overview_text(diagnostics)

        self.assertIn("Connected", text)
        self.assertIn("GamerHQ", text)
        self.assertIn("ACTIVE: 1", text)
        self.assertIn("Blocked: 3", text)
        self.assertIn("Failed: 6", text)
        self.assertNotIn("must-not-render", text)
        self.assertNotIn("secret", text.lower())

    def test_connect_modal_never_formats_submitted_token_into_user_text(self):
        source = inspect.getsource(awin_management.AwinConnectModal.on_submit)
        self.assertIn('"accessToken": str(self.access_token.value)', source)
        self.assertNotIn("self.access_token.value}", source)
        self.assertNotIn("repr(self.access_token", source)

    def test_saved_html_authority_requires_explicit_review_and_confirmation(self):
        submit_source = inspect.getsource(awin_management.AwinSavedHtmlModal.on_submit)
        confirm_source = inspect.getsource(
            awin_management.AwinSavedHtmlAuthorityConfirmView.confirm
        )
        helper_source = inspect.getsource(awin_management._run_saved_html_import)

        self.assertIn("if complete:", submit_source)
        self.assertIn("AwinSavedHtmlAuthorityConfirmView", submit_source)
        self.assertNotIn('completeAdvertiserId"] = complete', submit_source)
        self.assertIn("complete_advertiser_id=self.complete_advertiser_id", confirm_source)
        self.assertIn('payload["completeAdvertiserId"]', helper_source)
        self.assertIn("IMPORT_SAVED_HTML_API", helper_source)

    def test_post_confirmation_forwards_skill_confirm_payload_unchanged(self):
        init_source = inspect.getsource(awin_management.AwinPostConfirmView.__init__)
        confirm_source = inspect.getsource(awin_management.AwinPostConfirmView.confirm)
        preview_source = inspect.getsource(awin_management.AwinPostCopyModal.on_submit)

        self.assertIn('response.get("confirmPayload")', preview_source)
        self.assertIn("self.confirm_payload = dict(confirm_payload)", init_source)
        self.assertIn("POST_SEND_API", confirm_source)
        self.assertIn("dict(self.confirm_payload)", confirm_source)
        self.assertNotIn("CREATIVES_LIST_API", confirm_source)

    def test_campaign_ui_uses_only_public_campaign_contracts(self):
        source = inspect.getsource(awin_management)
        for contract in (
            "awin-affiliate.campaigns.list.v1",
            "awin-affiliate.campaigns.get.v1",
            "awin-affiliate.campaigns.create.v1",
            "awin-affiliate.campaigns.update.v1",
            "awin-affiliate.campaigns.set-active.v1",
            "awin-affiliate.campaigns.delete.v1",
            "awin-affiliate.campaigns.run-now.v1",
            "awin-affiliate.campaigns.preview-next.v1",
            "awin-affiliate.campaigns.history.v1",
        ):
            self.assertIn(contract, source)
        self.assertNotIn("scheduler.upsert", source)
        self.assertNotIn("campaigns.v1", source)


if __name__ == "__main__":
    unittest.main()

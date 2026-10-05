import inspect
import unittest

from cogs import server_management as management


class RecurringPostsManagementIntegrationTests(unittest.TestCase):
    def test_host_uses_versioned_recurring_posts_1_1_contracts(self):
        self.assertEqual(management._RECURRING_VALIDATE_API, "recurring-posts.validate.v1")
        self.assertEqual(management._RECURRING_UPDATE_API, "recurring-posts.update.v1")
        self.assertEqual(
            management._RECURRING_DELETE_PREVIEW_API,
            "recurring-posts.delete-preview.v1",
        )

    def test_v1_1_management_response_remains_compatible_with_host_parser(self):
        post = management._recurring_post(
            {
                "id": "post-1",
                "name": "Community reminder",
                "channelId": 10,
                "content": "Hello!",
                "schedule": {"type": "interval", "seconds": 3 * 60 * 60},
                "active": True,
                "status": "active",
                "scheduleSummary": "Every 180 minutes",
                "managementSummary": {"title": "Community reminder"},
                "quickActions": [],
            }
        )

        self.assertEqual(post.id, "post-1")
        self.assertEqual(post.channel_id, 10)
        self.assertTrue(post.active)
        self.assertEqual(management._recurring_schedule_label(post.schedule), "Every 3 hours")

    def test_review_text_is_clear_and_non_mutating(self):
        text = management._recurring_review_text(
            {
                "name": "Community reminder",
                "channelId": 10,
                "content": "Hello everyone!",
                "scheduleSummary": "Every 3 hours",
                "active": False,
            },
            editing=True,
        )

        self.assertIn("Review Recurring Post Changes", text)
        self.assertIn("Paused", text)
        self.assertIn("Every 3 hours", text)
        self.assertIn("Nothing changes until you confirm.", text)

    def test_interval_modal_communicates_server_minimum(self):
        source = inspect.getsource(management.RecurringPostModal)
        self.assertIn("Every N minutes (min. 15)", source)

    def test_edit_and_delete_flows_use_public_management_contracts(self):
        detail_source = inspect.getsource(management.RecurringPostDetailView)
        channel_source = inspect.getsource(management.RecurringPostEditChannelSelect)
        confirm_source = inspect.getsource(management.RecurringPostSaveConfirmView)

        self.assertIn("_RECURRING_DELETE_PREVIEW_API", detail_source)
        self.assertIn("_RECURRING_VALIDATE_API", channel_source)
        self.assertIn("_RECURRING_UPDATE_API", confirm_source)


if __name__ == "__main__":
    unittest.main()

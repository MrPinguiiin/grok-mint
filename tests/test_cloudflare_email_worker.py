import unittest
from unittest.mock import Mock, patch

import grok_core


def response(data, status_code=200):
    result = Mock()
    result.status_code = status_code
    result.json.return_value = data
    result.text = ""
    result.raise_for_status.side_effect = None
    return result


class CloudflareEmailWorkerTest(unittest.TestCase):
    def setUp(self):
        self.original_config = grok_core.config.copy()
        grok_core.config.update(
            {
                "cloudflare_api_base": "https://mail.example.workers.dev",
                "cloudflare_api_key": "worker-api-token",
                "cloudflare_auth_mode": "bearer",
                "cloudflare_path_accounts": "/api/new_address",
                "cloudflare_path_messages": "/api/mails",
            }
        )

    def tearDown(self):
        grok_core.config.clear()
        grok_core.config.update(self.original_config)

    @patch("requests.post")
    def test_create_uses_address_as_worker_mailbox_context(self, post):
        post.return_value = response({"ok": True, "address": "random@sendnrii.test"})

        address, credential = grok_core._cf_create()

        self.assertEqual(address, "random@sendnrii.test")
        self.assertEqual(credential, address)
        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"],
            "Bearer worker-api-token",
        )

    @patch("requests.get")
    def test_wait_code_filters_recipient_and_reads_nested_raw_message(self, get):
        get.side_effect = [
            response(
                {
                    "ok": True,
                    "messages": [{"id": 7, "subject": "Verify your email"}],
                }
            ),
            response(
                {
                    "ok": True,
                    "message": {
                        "id": 7,
                        "subject": "Verify your email",
                        "raw": "Your verification code: 123456",
                    },
                }
            ),
        ]

        code = grok_core._cf_wait_code("random@sendnrii.test", timeout=1, interval=0)

        self.assertEqual(code, "123456")
        inbox_call = get.call_args_list[0]
        self.assertEqual(inbox_call.kwargs["params"]["recipient"], "random@sendnrii.test")
        self.assertEqual(
            inbox_call.kwargs["headers"]["Authorization"],
            "Bearer worker-api-token",
        )

    @patch("requests.post")
    def test_create_keeps_legacy_mailbox_jwt(self, post):
        post.return_value = response(
            {"address": "legacy@example.test", "jwt": "mailbox-jwt"}
        )

        self.assertEqual(grok_core._cf_create(), ("legacy@example.test", "mailbox-jwt"))


if __name__ == "__main__":
    unittest.main()

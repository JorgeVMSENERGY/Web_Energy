"""Offline receiver tests; no AWS credentials, network or third-party packages needed."""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


def load_receiver(environment=None):
    boto = types.ModuleType("boto3")
    boto.resource = MagicMock()
    boto.client = MagicMock(side_effect=lambda *args, **kwargs: MagicMock())
    exceptions = types.ModuleType("botocore.exceptions")
    exceptions.ClientError = type("ClientError", (Exception,), {})
    config = types.ModuleType("botocore.config")
    config.Config = MagicMock()
    modules = {"boto3": boto, "botocore": types.ModuleType("botocore"),
               "botocore.exceptions": exceptions, "botocore.config": config}
    env = {"TABLE_NAME": "test", "ATTACHMENT_BUCKET": "test",
           "NOTIFICATION_QUEUE_URL": "test", "TURNSTILE_ENABLED": "false",
           "TURNSTILE_HOSTNAME": "", "TURNSTILE_HOSTNAMES": "[]"}
    env.update(environment or {})
    spec = importlib.util.spec_from_file_location(
        "receiver_under_test", Path(__file__).parents[1] / "lambda/receiver/app.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules), patch.dict(os.environ, env):
        spec.loader.exec_module(module)
    return module


app = load_receiver()


class TurnstileTests(unittest.TestCase):
    def setUp(self):
        for name, value in {
            "TURNSTILE_ENABLED": True, "TURNSTILE_SECRET_ARN": "arn:test:staging",
            "TURNSTILE_HOSTNAME": "jorgevmsenergy.github.io",
            "TURNSTILE_HOSTNAMES": '["jorgevmsenergy.github.io"]',
            "TABLE": MagicMock(), "SQS": MagicMock(), "S3": MagicMock(),
            "SECRETS": MagicMock(),
        }.items():
            item = patch.object(app, name, value)
            item.start()
            self.addCleanup(item.stop)
        app.SECRETS.get_secret_value.return_value = {"SecretString": "private-test-secret"}
        self.payload = {"form_type": "contact", "privacy_accepted": True,
                        "fields": {"email": "visitor@example.com"},
                        "turnstile_token": "private-test-token"}
        self.event = {"routeKey": "POST /submissions", "body": json.dumps(self.payload)}

    def verify_response(self, result):
        response = MagicMock()
        response.__enter__.return_value = io.BytesIO(json.dumps(result).encode())
        return patch.object(app.urllib.request, "urlopen", return_value=response)

    def accepted(self, **changes):
        return dict({"success": True, "hostname": "jorgevmsenergy.github.io",
                     "action": "contact"}, **changes)

    def assert_no_writes(self):
        app.TABLE.put_item.assert_not_called()
        app.SQS.send_message.assert_not_called()
        app.S3.copy_object.assert_not_called()
        app.S3.delete_object.assert_not_called()

    def test_positive_and_no_token_persisted(self):
        with self.verify_response(self.accepted()) as request:
            result = app.lambda_handler(self.event, None)
        self.assertEqual(result["statusCode"], 202)
        self.assertEqual(request.call_args.kwargs["timeout"], 5)
        self.assertIn(b"response=private-test-token", request.call_args.args[0].data)
        stored = repr(app.TABLE.put_item.call_args) + repr(app.SQS.send_message.call_args)
        self.assertNotIn("private-test-token", stored)
        self.assertNotIn("private-test-secret", stored)
        app.SQS.send_message.assert_called_once()

    def test_invalid_expired_duplicate_hostname_action(self):
        cases = [
            {"success": False, "error-codes": ["invalid-input-response"]},
            {"success": False, "error-codes": ["timeout-or-duplicate"]},  # expired
            {"success": False, "error-codes": ["timeout-or-duplicate"]},  # reused
            self.accepted(hostname="evil.jorgevmsenergy.github.io"),
            self.accepted(action="careers"),
            self.accepted(hostname="vmsenergy.com"),
        ]
        for case in cases:
            with self.subTest(case=case), self.verify_response(case):
                self.assertEqual(app.lambda_handler(self.event, None)["statusCode"], 400)
                self.assert_no_writes()

    def test_all_form_actions(self):
        for form_type in app.FORM_TYPES:
            with self.subTest(form_type=form_type), self.verify_response(self.accepted(action=form_type)):
                app.verify_turnstile(self.payload, form_type)

    def test_same_token_second_redemption_rejected(self):
        success = MagicMock()
        success.__enter__.return_value = io.BytesIO(json.dumps(self.accepted()).encode())
        duplicate = MagicMock()
        duplicate.__enter__.return_value = io.BytesIO(json.dumps(
            {"success": False, "error-codes": ["timeout-or-duplicate"]}).encode())
        with patch.object(app.urllib.request, "urlopen", side_effect=[success, duplicate]):
            self.assertEqual(app.lambda_handler(self.event, None)["statusCode"], 202)
            self.assertEqual(app.lambda_handler(self.event, None)["statusCode"], 400)
        app.TABLE.put_item.assert_called_once()
        app.SQS.send_message.assert_called_once()

    def test_success_preserves_attachment_flow(self):
        session = "b3150d9a-a404-4bdf-8dd4-3909398b017f"
        self.payload.update(upload_session_id=session, attachments=[{
            "name": "cv.pdf", "size": 100, "content_type": "application/pdf",
            "key": "pending/" + session + "/cv.pdf"}])
        app.S3.head_object.return_value = {"ContentLength": 100, "ContentType": "application/pdf"}
        self.event["body"] = json.dumps(self.payload)
        with self.verify_response(self.accepted()):
            self.assertEqual(app.lambda_handler(self.event, None)["statusCode"], 202)
        app.S3.copy_object.assert_called_once()
        app.S3.delete_object.assert_called_once()
        self.assertEqual(len(app.TABLE.put_item.call_args.kwargs["Item"]["attachments"]), 1)

    def test_provider_errors_do_not_log_credentials(self):
        with patch.object(app.urllib.request, "urlopen", side_effect=TimeoutError("private-test-secret")), \
                patch.object(app.LOGGER, "exception") as log:
            self.assertEqual(app.lambda_handler(self.event, None)["statusCode"], 503)
            log.assert_not_called()

    def test_missing_blank_wrong_type_oversize(self):
        for token in [None, "", " ", 42, "x" * 2049]:
            with self.subTest(token_type=type(token)), patch.object(app.urllib.request, "urlopen") as request:
                payload = dict(self.payload, turnstile_token=token)
                event = dict(self.event, body=json.dumps(payload))
                self.assertEqual(app.lambda_handler(event, None)["statusCode"], 400)
                request.assert_not_called()
                self.assert_no_writes()

    def test_timeout_and_network_error(self):
        for error in [TimeoutError("private-test-secret"), OSError("private-test-token")]:
            with self.subTest(error=type(error)), patch.object(app.urllib.request, "urlopen", side_effect=error):
                result = app.lambda_handler(self.event, None)
                self.assertEqual(result["statusCode"], 503)
                self.assertNotIn("private-test", result["body"])
                self.assert_no_writes()

    def test_secret_unavailable(self):
        app.SECRETS.get_secret_value.side_effect = Exception("AccessDenied")
        with patch.object(app.urllib.request, "urlopen") as request:
            self.assertEqual(app.lambda_handler(self.event, None)["statusCode"], 503)
            request.assert_not_called()
        self.assert_no_writes()

    def test_bad_response_and_provider_configuration(self):
        for result in [[], {}, {"success": "true"}, {"success": False, "error-codes": ["internal-error"]},
                       {"success": False, "error-codes": ["invalid-input-secret"]},
                       {"success": False, "error-codes": 42}]:
            with self.subTest(result=result), self.verify_response(result):
                self.assertEqual(app.lambda_handler(self.event, None)["statusCode"], 503)
                self.assert_no_writes()

    def test_disabled_preserves_production(self):
        with patch.object(app, "TURNSTILE_ENABLED", False), patch.object(app, "ENVIRONMENT", "prod"), \
                patch.object(app.urllib.request, "urlopen") as request:
            del self.payload["turnstile_token"]
            self.event["body"] = json.dumps(self.payload)
            self.assertEqual(app.lambda_handler(self.event, None)["statusCode"], 202)
            request.assert_not_called()
            app.SECRETS.get_secret_value.assert_not_called()

    def test_legacy_staging_hostname_remains_supported(self):
        with patch.object(app, "TURNSTILE_HOSTNAMES", None), self.verify_response(self.accepted()):
            self.assertEqual(app.lambda_handler(self.event, None)["statusCode"], 202)

    def test_explicit_hostnames_never_fall_back_to_legacy(self):
        for config in ["[]", "null", '"vmsenergy.com"', "{}", "not-json",
                       '["*.vmsenergy.com"]', '[""]', '[null]']:
            with self.subTest(config=config), patch.object(app, "TURNSTILE_HOSTNAMES", config), \
                    patch.object(app.urllib.request, "urlopen") as request:
                self.assertEqual(app.lambda_handler(self.event, None)["statusCode"], 503)
                request.assert_not_called()
                self.assert_no_writes()

    def test_staging_rejects_both_production_hosts(self):
        for host in ["vmsenergy.com", "www.vmsenergy.com"]:
            with self.subTest(host=host), self.verify_response(self.accepted(hostname=host)):
                self.assertEqual(app.lambda_handler(self.event, None)["statusCode"], 400)
                self.assert_no_writes()

    def production_settings(self):
        return patch.multiple(app, ENVIRONMENT="prod", TURNSTILE_ENABLED=True,
                              TURNSTILE_SECRET_ARN="arn:test:prod",
                              TURNSTILE_HOSTNAMES='["vmsenergy.com","www.vmsenergy.com"]')

    def test_production_accepts_both_exact_hosts_when_enabled(self):
        for host in ["vmsenergy.com", "www.vmsenergy.com"]:
            with self.subTest(host=host), self.production_settings(), \
                    self.verify_response(self.accepted(hostname=host)):
                self.assertEqual(app.lambda_handler(self.event, None)["statusCode"], 202)
                app.SECRETS.get_secret_value.assert_called_with(SecretId="arn:test:prod")

    def test_production_rejects_other_hosts_and_actions(self):
        cases = [self.accepted(hostname=host) for host in [
            "jorgevmsenergy.github.io", "evil.vmsenergy.com", "vmsenergy.com.evil.com",
            "evilvmsenergy.com", "www.www.vmsenergy.com", "vmsenergy.com.", "VMSENERGY.COM", ""]]
        cases.append(self.accepted(hostname="vmsenergy.com", action="careers"))
        for result in cases:
            with self.subTest(result=result), self.production_settings(), self.verify_response(result):
                self.assertEqual(app.lambda_handler(self.event, None)["statusCode"], 400)
                self.assert_no_writes()

    def test_production_invalid_and_reused_tokens(self):
        for code in ["invalid-input-response", "timeout-or-duplicate"]:
            with self.subTest(code=code), self.production_settings(), self.verify_response(
                    {"success": False, "error-codes": [code]}):
                self.assertEqual(app.lambda_handler(self.event, None)["statusCode"], 400)
                self.assert_no_writes()

    def test_production_requires_token_when_enabled(self):
        del self.payload["turnstile_token"]
        self.event["body"] = json.dumps(self.payload)
        with self.production_settings(), patch.object(app.urllib.request, "urlopen") as request:
            self.assertEqual(app.lambda_handler(self.event, None)["statusCode"], 400)
            request.assert_not_called()
            self.assert_no_writes()

    def test_production_missing_secret_fails_closed_when_enabled(self):
        app.SECRETS.get_secret_value.side_effect = Exception("Secret has no version")
        with self.production_settings(), patch.object(app.urllib.request, "urlopen") as request:
            self.assertEqual(app.lambda_handler(self.event, None)["statusCode"], 503)
            request.assert_not_called()
            self.assert_no_writes()

    def test_provisioned_production_secret_does_not_enable_validation(self):
        module = load_receiver({"ENVIRONMENT": "prod", "TURNSTILE_ENABLED": "false",
                                "TURNSTILE_SECRET_ARN": "arn:test:prod",
                                "TURNSTILE_HOSTNAMES": '["vmsenergy.com","www.vmsenergy.com"]'})
        self.assertFalse(module.TURNSTILE_ENABLED)
        with patch.object(module.urllib.request, "urlopen") as request:
            module.verify_turnstile({}, "contact")
            request.assert_not_called()
            module.SECRETS.get_secret_value.assert_not_called()

    def test_presign_does_not_consume_token(self):
        payload = {"form_type": "careers", "files": [
            {"name": "cv.pdf", "size": 100, "content_type": "application/pdf"}]}
        event = {"routeKey": "POST /uploads/presign", "body": json.dumps(payload)}
        with patch.object(app.urllib.request, "urlopen") as request:
            app.S3.generate_presigned_url.return_value = "https://upload.example.com"
            self.assertEqual(app.lambda_handler(event, None)["statusCode"], 200)
            request.assert_not_called()
            app.SECRETS.get_secret_value.assert_not_called()

    def test_failure_precedes_attachment_finalization(self):
        self.payload.update(attachments=[{"name": "cv.pdf"}],
                            upload_session_id="b3150d9a-a404-4bdf-8dd4-3909398b017f")
        self.event["body"] = json.dumps(self.payload)
        with self.verify_response({"success": False}), patch.object(app, "finalize_attachments") as finalize:
            self.assertEqual(app.lambda_handler(self.event, None)["statusCode"], 400)
            finalize.assert_not_called()
        self.assert_no_writes()

    def test_reject_token_in_fields(self):
        self.payload["fields"]["cf-turnstile-response"] = "private-test-token"
        self.event["body"] = json.dumps(self.payload)
        self.assertEqual(app.lambda_handler(self.event, None)["statusCode"], 400)
        self.assert_no_writes()


if __name__ == "__main__":
    unittest.main()

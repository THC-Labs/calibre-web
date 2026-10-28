import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import cps
if not cps.cli_param.gd_path:
    cps.cli_param.gd_path = "/tmp/test_gd.db"

from flask import Flask, json
from cps.web import web


class TestHealthzEndpoint(unittest.TestCase):

    def setUp(self):
        self.app = Flask(__name__)
        self.app.config["SECRET_KEY"] = "test-healthz-secret"
        self.app.register_blueprint(web)
        self.client = self.app.test_client()

    def test_healthz_returns_200_and_json(self):
        response = self.client.get('/healthz')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data.get("status"), "healthy")
        self.assertEqual(data.get("service"), "calibre-web")

    def test_healthz_unconfigured_db_not_redirected(self):
        from cps.admin import before_request
        from flask import request as flask_request

        mock_attrs = {
            'db_configured': False,
            'config_public_reg': False,
            'config_anonbrowse': False,
            'config_uploading': False,
            'config_theme': 0,
            'config_authors_max': 10,
        }
        with self.app.test_request_context('/healthz'):
            self.assertEqual(flask_request.endpoint, 'web.healthz')
            with patch.multiple(cps.config, **mock_attrs, create=True):
                result = before_request()
                self.assertIsNone(result, "/healthz must not be redirected when DB is unconfigured")


class TestEnvironmentNamespaceResolution(unittest.TestCase):

    def setUp(self):
        from cps.config_sql import ConfigSQL
        self.config = ConfigSQL()

    def test_port_precedence(self):
        with patch.dict(os.environ, {"CALIBRE_PORT": "9001", "PORT": "9002"}):
            self.assertEqual(self.config.get_config_port(), 9001)

        with patch.dict(os.environ, {"PORT": "9002"}, clear=True):
            self.assertEqual(self.config.get_config_port(), 9002)

        with patch.dict(os.environ, {"CALIBRE_WEB_PORT": "9003"}, clear=True):
            self.assertEqual(self.config.get_config_port(), 9003)

    def test_backbone_env_precedence(self):
        with patch.dict(os.environ, {
            "CALIBRE_BACKBONE_ENABLED": "true",
            "CALIBRE_BACKBONE_URL": "http://calibre-auth:8080",
            "CALIBRE_BACKBONE_INTERNAL_URL": "http://internal-auth:8080",
            "CALIBRE_BACKBONE_JWT_SECRET": "calibre-secret-123",
            "CALIBRE_BACKBONE_AUTO_REGISTER": "true"
        }, clear=True):
            self.assertTrue(self.config.is_backbone_enabled())
            self.assertEqual(self.config.get_backbone_url(), "http://calibre-auth:8080")
            self.assertEqual(self.config.get_backbone_internal_url(), "http://internal-auth:8080")
            self.assertEqual(self.config.get_backbone_jwt_secret(), "calibre-secret-123")
            self.assertTrue(self.config.is_backbone_auto_register())

        with patch.dict(os.environ, {
            "BACKBONE_ENABLED": "true",
            "BACKBONE_URL": "http://generic-auth:8080",
            "BACKBONE_INTERNAL_URL": "http://generic-internal:8080",
            "JWT_SECRET": "generic-secret-456",
            "BACKBONE_AUTO_REGISTER": "false"
        }, clear=True):
            self.assertTrue(self.config.is_backbone_enabled())
            self.assertEqual(self.config.get_backbone_url(), "http://generic-auth:8080")
            self.assertEqual(self.config.get_backbone_internal_url(), "http://generic-internal:8080")
            self.assertEqual(self.config.get_backbone_jwt_secret(), "generic-secret-456")
            self.assertFalse(self.config.is_backbone_auto_register())


if __name__ == '__main__':
    unittest.main()

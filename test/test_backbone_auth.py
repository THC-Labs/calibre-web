import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import base64
import hashlib
import hmac
import json
import time
import unittest
from unittest.mock import patch, MagicMock

from cps.backbone_auth import (
    decode_base64url,
    encode_base64url,
    verify_backbone_jwt,
    exchange_code_for_jwt,
)


def create_test_jwt(secret: str, payload_data: dict, alg: str = "HS256") -> str:
    """Helper to create a JWT formatted exactly like Spring Boot JJWT."""
    header = {"alg": alg, "typ": "JWT"}
    header_json = json.dumps(header, separators=(',', ':')).encode('utf-8')
    header_b64 = encode_base64url(header_json)

    payload_json = json.dumps(payload_data, separators=(',', ':')).encode('utf-8')
    payload_b64 = encode_base64url(payload_json)

    message = f"{header_b64}.{payload_b64}".encode('utf-8')
    signature = hmac.new(secret.encode('utf-8'), message, hashlib.sha256).digest()
    sig_b64 = encode_base64url(signature)

    return f"{header_b64}.{payload_b64}.{sig_b64}"


class TestBackboneJwtVerification(unittest.TestCase):

    def setUp(self):
        self.secret = "my-test-super-secret-key-32-chars-long!"
        self.user_uuid = "e8913926-2819-4a92-9694-54129df27e28"
        self.discord_id = "987654321012345678"
        self.username = "test_discord_user"

    def test_valid_jwt_verification(self):
        now = time.time()
        payload = {
            "sub": self.user_uuid,
            "discordId": self.discord_id,
            "username": self.username,
            "iat": int(now),
            "exp": int(now + 3600),
        }
        token = create_test_jwt(self.secret, payload)

        valid, claims, error = verify_backbone_jwt(token, self.secret)
        self.assertTrue(valid, f"Verification failed with error: {error}")
        self.assertIsNone(error)
        self.assertIsNotNone(claims)
        self.assertEqual(claims["sub"], self.user_uuid)
        self.assertEqual(claims["discordId"], self.discord_id)
        self.assertEqual(claims["username"], self.username)

    def test_invalid_signature_rejected(self):
        now = time.time()
        payload = {
            "sub": self.user_uuid,
            "discordId": self.discord_id,
            "username": self.username,
            "iat": int(now),
            "exp": int(now + 3600),
        }
        token = create_test_jwt(self.secret, payload)
        wrong_secret = "completely-wrong-secret-key-abcdef!"

        valid, claims, error = verify_backbone_jwt(token, wrong_secret)
        self.assertFalse(valid)
        self.assertIsNone(claims)
        self.assertIn("Invalid JWT signature", error)

    def test_expired_jwt_rejected(self):
        now = time.time()
        payload = {
            "sub": self.user_uuid,
            "discordId": self.discord_id,
            "username": self.username,
            "iat": int(now - 7200),
            "exp": int(now - 3600),  # expired 1 hour ago
        }
        token = create_test_jwt(self.secret, payload)

        valid, claims, error = verify_backbone_jwt(token, self.secret)
        self.assertFalse(valid)
        self.assertIsNone(claims)
        self.assertIn("expired", error)

    def test_malformed_token_rejected(self):
        valid, claims, error = verify_backbone_jwt("not-a-token", self.secret)
        self.assertFalse(valid)
        self.assertIn("expected 3 segments", error)

        valid, claims, error = verify_backbone_jwt("", self.secret)
        self.assertFalse(valid)

    def test_unsupported_algorithm_rejected(self):
        payload = {"sub": self.user_uuid, "exp": int(time.time() + 3600)}
        token = create_test_jwt(self.secret, payload, alg="none")

        valid, claims, error = verify_backbone_jwt(token, self.secret)
        self.assertFalse(valid)
        self.assertIn("Unsupported JWT algorithm", error)

    def test_missing_sub_rejected(self):
        payload = {"discordId": self.discord_id, "exp": int(time.time() + 3600)}
        token = create_test_jwt(self.secret, payload)

        valid, claims, error = verify_backbone_jwt(token, self.secret)
        self.assertFalse(valid)
        self.assertIn("missing required subject", error)


class TestBackboneTokenExchange(unittest.TestCase):

    @patch("requests.post")
    def test_successful_token_exchange(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"token": "jwt.sample.token"}
        mock_post.return_value = mock_response

        token, error = exchange_code_for_jwt("http://localhost:8080", "valid_code_123")
        self.assertEqual(token, "jwt.sample.token")
        self.assertIsNone(error)
        mock_post.assert_called_once_with(
            "http://localhost:8080/auth/token",
            params={"code": "valid_code_123"},
            timeout=(5.0, 10.0),
        )

    @patch("requests.post")
    def test_expired_code_exchange(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"error": "Invalid or expired code"}
        mock_post.return_value = mock_response

        token, error = exchange_code_for_jwt("http://localhost:8080", "expired_code_999")
        self.assertIsNone(token)
        self.assertEqual(error, "Invalid or expired code")

    @patch("requests.post")
    def test_network_failure_exchange(self, mock_post):
        import requests
        mock_post.side_effect = requests.exceptions.ConnectionError("Connection refused")

        token, error = exchange_code_for_jwt("http://localhost:8080", "code_abc")
        self.assertIsNone(token)
        self.assertIn("Network error connecting to Backbone", error)


class TestBackboneAuthRoutes(unittest.TestCase):

    def setUp(self):
        from flask import Flask
        from flask_babel import Babel
        from cps.cw_login import LoginManager
        from cps.backbone_auth import backbone_auth
        self.app = Flask(__name__)
        self.app.config["SECRET_KEY"] = "unit-test-secret"
        self.babel = Babel(self.app)
        self.login_manager = LoginManager()
        self.login_manager.init_app(self.app)
        @self.login_manager.user_loader
        def load_user(user_id, random=None, session_key=None):
            return None
        import cps
        cps.config.config_allow_reverse_proxy_header_login = False
        self.app.register_blueprint(backbone_auth)

        # Register dummy web endpoints to satisfy redirects
        from flask import Blueprint
        web_bp = Blueprint('web', __name__)
        @web_bp.route('/login')
        def login():
            return "login_page"
        @web_bp.route('/')
        def index():
            return "index_page"
        @web_bp.route('/me')
        def profile():
            return "profile_page"
        self.app.register_blueprint(web_bp)

        self.client = self.app.test_client()

    @patch("cps.config.is_backbone_enabled", return_value=False)
    def test_login_disabled_redirects_to_login(self, mock_enabled):
        response = self.client.get('/login/thc')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    @patch("cps.config.get_backbone_url", return_value="http://localhost:8080")
    @patch("cps.config.is_backbone_enabled", return_value=True)
    def test_login_enabled_redirects_to_backbone(self, mock_enabled, mock_url):
        with self.client as client:
            response = client.get('/login/thc?next=%2Fcatalog')
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.headers['Location'], 'http://localhost:8080/oauth2/authorization/discord')
            # Verify session was populated
            from flask import session
            self.assertIn('thc_backbone_state', session)
            self.assertEqual(session.get('thc_backbone_next'), '/catalog')

    @patch("cps.config.is_backbone_enabled", return_value=True)
    def test_callback_missing_code_redirects_to_login(self, mock_enabled):
        response = self.client.get('/auth/callback')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    @patch("cps.backbone_auth.exchange_code_for_jwt", return_value=(None, "Code expired"))
    @patch("cps.config.is_backbone_enabled", return_value=True)
    def test_callback_exchange_failure_redirects_to_login(self, mock_enabled, mock_exchange):
        response = self.client.get('/auth/callback?code=expired_123')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    @patch("cps.backbone_auth.verify_backbone_jwt", return_value=(False, None, "Bad signature"))
    @patch("cps.backbone_auth.exchange_code_for_jwt", return_value=("fake_jwt", None))
    @patch("cps.config.is_backbone_enabled", return_value=True)
    def test_callback_jwt_invalid_redirects_to_login(self, mock_enabled, mock_exchange, mock_verify):
        response = self.client.get('/auth/callback?code=valid_code')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    @patch("cps.backbone_auth.login_user")
    @patch("cps.ub.session")
    @patch("cps.config.is_backbone_auto_register", return_value=True)
    @patch("cps.backbone_auth.verify_backbone_jwt")
    @patch("cps.backbone_auth.exchange_code_for_jwt", return_value=("valid_jwt", None))
    @patch("cps.config.is_backbone_enabled", return_value=True)
    def test_callback_success_auto_register_new_user(self, mock_enabled, mock_exchange, mock_verify,
                                                    mock_auto_reg, mock_session, mock_login):
        mock_verify.return_value = (True, {
            "sub": "user-uuid-1234",
            "discordId": "555666777",
            "username": "new_discord_hero"
        }, None)

        # Mock no existing OAuth link and no existing username collision
        mock_query = MagicMock()
        mock_session.query.return_value = mock_query
        mock_query.filter_by.return_value.first.return_value = None
        mock_query.filter.return_value.first.return_value = None

        response = self.client.get('/auth/callback?code=auth_code_xyz')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/', response.headers['Location'])
        self.assertTrue(mock_session.add.called)
        self.assertTrue(mock_session.commit.called)
        self.assertTrue(mock_login.called)

    @patch("cps.backbone_auth.login_user")
    @patch("cps.ub.session")
    @patch("cps.backbone_auth.verify_backbone_jwt")
    @patch("cps.backbone_auth.exchange_code_for_jwt", return_value=("valid_jwt", None))
    @patch("cps.config.is_backbone_enabled", return_value=True)
    def test_callback_success_existing_linked_user(self, mock_enabled, mock_exchange, mock_verify,
                                                  mock_session, mock_login):
        mock_verify.return_value = (True, {
            "sub": "user-uuid-1234",
            "discordId": "555666777",
            "username": "existing_user"
        }, None)

        existing_user = MagicMock()
        existing_user.name = "existing_user"

        oauth_entry = MagicMock()
        oauth_entry.user = existing_user

        mock_query = MagicMock()
        mock_session.query.return_value = mock_query
        mock_query.filter_by.return_value.first.return_value = oauth_entry

        response = self.client.get('/auth/callback?code=auth_code_xyz')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/', response.headers['Location'])
        self.assertTrue(mock_login.called)
        self.assertEqual(oauth_entry.token, "valid_jwt")

    @patch("cps.backbone_auth.current_user")
    @patch("cps.ub.session")
    def test_unlink_success(self, mock_session, mock_current_user):
        self.app.config["LOGIN_DISABLED"] = True
        mock_current_user.is_authenticated = True
        mock_current_user.id = 42
        mock_current_user.name = "test_user"

        oauth_entry = MagicMock()
        mock_query = MagicMock()
        mock_session.query.return_value = mock_query
        mock_query.filter_by.return_value.first.return_value = oauth_entry

        response = self.client.get('/unlink/thc')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/me', response.headers['Location'])
        self.assertTrue(mock_session.delete.called)
        self.assertTrue(mock_session.commit.called)

    @patch("cps.backbone_auth.current_user")
    @patch("cps.ub.session")
    def test_unlink_no_link(self, mock_session, mock_current_user):
        self.app.config["LOGIN_DISABLED"] = True
        mock_current_user.is_authenticated = True
        mock_current_user.id = 42
        mock_current_user.name = "test_user"

        mock_query = MagicMock()
        mock_session.query.return_value = mock_query
        mock_query.filter_by.return_value.first.return_value = None

        response = self.client.get('/unlink/thc')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/me', response.headers['Location'])
        self.assertFalse(mock_session.delete.called)


if __name__ == '__main__':
    unittest.main()

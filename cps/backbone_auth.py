# -*- coding: utf-8 -*-

#  This file is part of Calibre-Web (https://github.com/janeczku/calibre-web)
#  Integration with THC Labs Backbone Discord Authentication.
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program. If not, see <http://www.gnu.org/licenses/>.

import base64
import hmac
import hashlib
import json
import secrets
import time
from typing import Optional, Tuple, Dict, Any

import requests
from flask import Blueprint, request, session, redirect, url_for, flash
from flask_babel import gettext as _
from werkzeug.security import generate_password_hash
from sqlalchemy.sql.expression import func

from . import logger, config, ub, constants
from .cw_login import login_user, current_user
from .usermanagement import user_login_required
from .redirect import get_redirect_location
from .string_helper import strip_whitespaces
from urllib.parse import urlencode

log = logger.create()

backbone_auth = Blueprint('backbone_auth', __name__)


def decode_base64url(s: str) -> bytes:
    """Safely decode a base64url-encoded string with padding correction."""
    rem = len(s) % 4
    if rem > 0:
        s += '=' * (4 - rem)
    return base64.urlsafe_b64decode(s.encode('ascii'))


def encode_base64url(data: bytes) -> str:
    """Encode bytes to a base64url string without trailing padding."""
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')


def verify_backbone_jwt(token: str, secret: str, leeway: int = 30) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    """
    Verify and decode a JSON Web Token issued by THC Labs Backbone (HS256).

    :param token: The raw JWT string (header.payload.signature)
    :param secret: The HMAC secret key (jwt.secret in backbone)
    :param leeway: Clock skew allowance in seconds for expiration check
    :return: (is_valid, payload_dict, error_message)
    """
    if not token or not isinstance(token, str):
        return False, None, "Token must be a non-empty string"

    parts = token.split('.')
    if len(parts) != 3:
        return False, None, "Invalid JWT format: expected 3 segments"

    header_b64, payload_b64, signature_b64 = parts

    # Parse header
    try:
        header_bytes = decode_base64url(header_b64)
        header = json.loads(header_bytes.decode('utf-8'))
    except Exception as e:
        return False, None, f"Failed to decode JWT header: {e}"

    SUPPORTED_ALGS = {
        "HS256": hashlib.sha256,
        "HS384": hashlib.sha384,
        "HS512": hashlib.sha512,
    }
    alg = header.get("alg")
    if alg not in SUPPORTED_ALGS:
        return False, None, f"Unsupported JWT algorithm: {alg} (expected one of {', '.join(SUPPORTED_ALGS.keys())})"

    # Verify signature
    if not secret:
        log.warning("Backbone JWT secret is empty or not configured")

    message = f"{header_b64}.{payload_b64}".encode('utf-8')
    expected_sig = hmac.new(secret.encode('utf-8'), message, SUPPORTED_ALGS[alg]).digest()
    expected_sig_b64 = encode_base64url(expected_sig)

    if not hmac.compare_digest(signature_b64, expected_sig_b64):
        return False, None, "Invalid JWT signature"

    # Parse payload
    try:
        payload_bytes = decode_base64url(payload_b64)
        payload = json.loads(payload_bytes.decode('utf-8'))
    except Exception as e:
        return False, None, f"Failed to decode JWT payload: {e}"

    # Verify expiration
    now = time.time()
    exp = payload.get("exp")
    if exp is not None:
        try:
            exp_timestamp = float(exp)
            if exp_timestamp < (now - leeway):
                return False, None, "JWT token has expired"
        except (ValueError, TypeError):
            return False, None, "Invalid exp claim format in JWT"

    # Verify subject
    if not payload.get("sub"):
        return False, None, "JWT missing required subject (sub) claim"

    return True, payload, None


def exchange_code_for_jwt(
    backbone_url: str,
    code: str,
    timeout: Tuple[float, float] = (5.0, 10.0)
) -> Tuple[Optional[str], Optional[str]]:
    """
    Exchange a single-use authorization code for a JWT via Backbone POST /auth/token?code={code}.

    :param backbone_url: Base URL of the backbone server
    :param code: One-time authorization code received in callback
    :param timeout: (connect_timeout, read_timeout)
    :return: (jwt_token, error_message)
    """
    token_endpoint = f"{backbone_url.rstrip('/')}/auth/token"
    try:
        response = requests.post(token_endpoint, params={"code": code}, timeout=timeout)
        if response.status_code == 200:
            data = response.json()
            jwt_token = data.get("token")
            if jwt_token:
                return jwt_token, None
            return None, "Backbone response missing 'token' field"
        else:
            try:
                err_data = response.json()
                err_msg = err_data.get("error", f"HTTP {response.status_code}")
            except Exception:
                err_msg = f"HTTP {response.status_code} ({response.text[:100]})"
            return None, err_msg
    except requests.exceptions.RequestException as e:
        log.error("Network error exchanging token with Backbone at %s: %s", token_endpoint, e)
        return None, f"Network error connecting to Backbone: {e}"


@backbone_auth.route('/login/thc')
def login():
    """Initiates the Discord authentication flow via THC Labs Backbone."""
    if not config.is_backbone_enabled():
        flash(_("THC Backbone authentication is not enabled"), category="error")
        return redirect(url_for('web.login'))

    if current_user and current_user.is_authenticated:
        return redirect(url_for('web.index'))

    # Store redirect target if provided
    next_url = request.args.get('next')
    if next_url:
        session['thc_backbone_next'] = next_url

    # Generate state token for CSRF protection
    state = secrets.token_urlsafe(32)
    session['thc_backbone_state'] = state

    backbone_url = config.get_backbone_url()
    callback_url = url_for('backbone_auth.callback', _external=True)
    params = {
        'redirect_uri': callback_url,
        'state': state
    }
    authorization_url = f"{backbone_url}/oauth2/authorization/discord?{urlencode(params)}"
    log.debug("Redirecting to Backbone authorization endpoint: %s", authorization_url)
    return redirect(authorization_url)


@backbone_auth.route('/auth/callback')
@backbone_auth.route('/login/thc/callback')
def callback():
    """
    Callback endpoint invoked by Backbone after Discord OAuth authorization.
    Backbone redirects to: {frontend-url}/auth/callback?code={code}
    """
    if not config.is_backbone_enabled():
        flash(_("THC Backbone authentication is not enabled"), category="error")
        return redirect(url_for('web.login'))

    code = request.args.get('code')
    if not code:
        err = request.args.get('error', _("Authorization code missing"))
        log.warning("Backbone callback reached without code parameter: %s", err)
        flash(_("Authentication failed: %(error)s", error=err), category="error")
        return redirect(url_for('web.login'))

    backbone_internal_url = config.get_backbone_internal_url()
    jwt_secret = config.get_backbone_jwt_secret()

    # Step 1: Exchange code for JWT
    jwt_token, err = exchange_code_for_jwt(backbone_internal_url, code)
    if not jwt_token:
        log.error("Failed to exchange code with Backbone: %s", err)
        flash(_("Could not complete Discord authentication: %(error)s", error=err), category="error")
        return redirect(url_for('web.login'))

    # Step 2: Cryptographically verify JWT
    valid, payload, err = verify_backbone_jwt(jwt_token, jwt_secret)
    if not valid or payload is None:
        log.error("Backbone JWT verification rejected: %s", err)
        flash(_("Security verification failed: %(error)s", error=err), category="error")
        return redirect(url_for('web.login'))

    sub = payload.get("sub")
    discord_id = str(payload.get("discordId") or "")
    username = payload.get("username")

    log.info("Backbone authentication validated for user '%s' (Discord ID: %s, Sub: %s)", username, discord_id, sub)

    # Identifier used to match identity link (prefer discord_id, fallback to sub UUID)
    provider_user_id = discord_id if discord_id else sub

    # Step 3A: User is already logged into Calibre-Web -> Account Linking Flow
    if current_user and current_user.is_authenticated:
        existing_link = ub.session.query(ub.OAuth).filter_by(
            provider=constants.PROVIDER_THC_BACKBONE,
            provider_user_id=provider_user_id
        ).first()

        if existing_link and existing_link.user_id != current_user.id:
            flash(_("This Discord account is already linked to another Calibre-Web user"), category="error")
            log.warning("User %s attempted to link Discord identity %s already owned by %s",
                        current_user.id, provider_user_id, existing_link.user_id)
            return redirect(url_for('web.profile'))

        if existing_link and existing_link.user_id == current_user.id:
            existing_link.token = jwt_token
            ub.session.commit()
            flash(_("Your Discord account is already linked"), category="info")
            return redirect(url_for('web.profile'))

        new_link = ub.OAuth(
            provider=constants.PROVIDER_THC_BACKBONE,
            provider_user_id=provider_user_id,
            token=jwt_token,
            user_id=current_user.id
        )
        ub.session.add(new_link)
        try:
            ub.session.commit()
            flash(_("Successfully linked Discord account!"), category="success")
            log.info("User %s linked Discord identity %s", current_user.name, provider_user_id)
        except Exception as e:
            ub.session.rollback()
            log.error_or_exception(e)
            flash(_("Database error linking account: %(error)s", error=e), category="error")
        return redirect(url_for('web.profile'))

    # Step 3B: User is unauthenticated -> Login or Auto-Register Flow
    oauth_entry = ub.session.query(ub.OAuth).filter_by(
        provider=constants.PROVIDER_THC_BACKBONE,
        provider_user_id=provider_user_id
    ).first()

    if oauth_entry and oauth_entry.user:
        oauth_entry.token = jwt_token
        ub.session.commit()
        login_user(oauth_entry.user, remember=True)
        flash(_("Welcome back, %(name)s!", name=oauth_entry.user.name), category="success")
        log.info("User '%s' logged in via THC Backbone Discord", oauth_entry.user.name)
        next_loc = session.pop('thc_backbone_next', None)
        return redirect(get_redirect_location(next_loc, "web.index"))

    # Identity not linked yet: check if auto-registration is permitted
    if not config.is_backbone_auto_register():
        flash(_("No Calibre-Web account is linked to this Discord profile and auto-registration is disabled."),
              category="error")
        log.warning("Discord user '%s' (%s) denied login: not linked and auto-register disabled", username, provider_user_id)
        return redirect(url_for('web.login'))

    # Determine unique local username
    raw_name = strip_whitespaces(username or f"discord_{provider_user_id[:8]}")
    candidate_name = raw_name

    existing_user = ub.session.query(ub.User).filter(func.lower(ub.User.name) == candidate_name.lower()).first()
    if existing_user:
        candidate_name = f"{raw_name}_thc"
        if ub.session.query(ub.User).filter(func.lower(ub.User.name) == candidate_name.lower()).first():
            candidate_name = f"{raw_name}_{provider_user_id[-4:]}"

    new_user = ub.User()
    new_user.name = candidate_name
    new_user.email = ""
    new_user.password = generate_password_hash(secrets.token_urlsafe(32))
    new_user.role = getattr(config, 'config_default_role', constants.ROLE_USER)
    new_user.locale = getattr(config, 'config_default_locale', 'en')
    new_user.sidebar_view = getattr(config, 'config_default_show', 1)
    new_user.allowed_tags = getattr(config, 'config_allowed_tags', "")
    new_user.denied_tags = getattr(config, 'config_denied_tags', "")
    new_user.allowed_column_value = getattr(config, 'config_allowed_column_value', "")
    new_user.denied_column_value = getattr(config, 'config_denied_column_value', "")

    try:
        ub.session.add(new_user)
        ub.session.flush()

        oauth_entry = ub.OAuth(
            provider=constants.PROVIDER_THC_BACKBONE,
            provider_user_id=provider_user_id,
            token=jwt_token,
            user_id=new_user.id
        )
        ub.session.add(oauth_entry)
        ub.session.commit()
        log.info("Auto-registered new user '%s' linked to Discord ID %s", new_user.name, provider_user_id)
    except Exception as e:
        ub.session.rollback()
        log.error_or_exception(e)
        flash(_("An error occurred while creating your account. Please try again later."), category="error")
        return redirect(url_for('web.login'))

    login_user(new_user, remember=True)
    flash(_("Welcome to Calibre-Web, %(name)s!", name=new_user.name), category="success")
    next_loc = session.pop('thc_backbone_next', None)
    return redirect(get_redirect_location(next_loc, "web.index"))


@backbone_auth.route('/unlink/thc')
@user_login_required
def unlink():
    """Unlinks the current user's Discord identity from their Calibre-Web account."""
    query = ub.session.query(ub.OAuth).filter_by(
        provider=constants.PROVIDER_THC_BACKBONE,
        user_id=current_user.id
    )
    entry = query.first()
    if entry:
        try:
            ub.session.delete(entry)
            ub.session.commit()
            flash(_("Unlinked Discord account successfully"), category="success")
            log.info("User '%s' unlinked their Discord account", current_user.name)
        except Exception as e:
            ub.session.rollback()
            log.error_or_exception(e)
            flash(_("Failed to unlink Discord account: %(error)s", error=e), category="error")
    else:
        flash(_("No Discord account linked to your profile"), category="info")
    return redirect(url_for('web.profile'))

"""
操作者面密碼（Basic Auth）授權邏輯的確定性單元測試（無 LLM、無網路）。

涵蓋 api.auth.check_admin_auth 與 is_kiosk_api —— 安全相關的純函式，先前只有
手動 curl 驗過，這裡補上自動化覆蓋。

跑法：
    pytest eval/test_admin_auth.py
"""

import base64

import pytest

import api.auth as auth


def _basic(user: str, pw: str) -> dict:
    """組一個帶 Basic Auth header 的 dict（dict.get 即可餵給 check_admin_auth）。"""
    token = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return {"authorization": f"Basic {token}"}


@pytest.fixture
def settings_state():
    """每個 test 跑前後保存／還原 _settings 上的密碼與 token，避免互相污染。"""
    s = auth._settings
    saved = (s.api_admin_password, s.api_local_token)
    yield s
    s.api_admin_password, s.api_local_token = saved


# ── check_admin_auth ────────────────────────────────────────────────

def test_unset_password_allows_everything(settings_state):
    settings_state.api_admin_password = ""
    assert auth.check_admin_auth({}) is True


def test_correct_basic_password(settings_state):
    settings_state.api_admin_password = "s3cret"
    assert auth.check_admin_auth(_basic("admin", "s3cret")) is True


def test_wrong_basic_password(settings_state):
    settings_state.api_admin_password = "s3cret"
    assert auth.check_admin_auth(_basic("admin", "nope")) is False


def test_username_is_ignored(settings_state):
    settings_state.api_admin_password = "s3cret"
    assert auth.check_admin_auth(_basic("anyone", "s3cret")) is True


def test_password_containing_colon(settings_state):
    settings_state.api_admin_password = "pa:ss:word"
    assert auth.check_admin_auth(_basic("admin", "pa:ss:word")) is True


def test_no_credentials_rejected(settings_state):
    settings_state.api_admin_password = "s3cret"
    settings_state.api_local_token = ""
    assert auth.check_admin_auth({}) is False


def test_malformed_basic_header_does_not_crash(settings_state):
    settings_state.api_admin_password = "s3cret"
    assert auth.check_admin_auth({"authorization": "Basic !!!not-base64!!!"}) is False


def test_token_fallback_passes(settings_state):
    settings_state.api_admin_password = "s3cret"
    settings_state.api_local_token = "tok123"
    assert auth.check_admin_auth({auth._TOKEN_HEADER: "tok123"}) is True


def test_wrong_token_rejected(settings_state):
    settings_state.api_admin_password = "s3cret"
    settings_state.api_local_token = "tok123"
    assert auth.check_admin_auth({auth._TOKEN_HEADER: "wrong"}) is False


# ── is_kiosk_api ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/chat/query"),
        ("POST", "/api/chat/reset"),
        ("GET", "/api/profiles"),
    ],
)
def test_kiosk_endpoints_recognised(method, path):
    assert auth.is_kiosk_api(method, path) is True


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/chat/ingest"),       # 管理：重索引
        ("POST", "/api/profiles/activate"),  # 管理：切 profile
        ("GET", "/api/dashboard/stats"),     # 管理：dashboard
        ("GET", "/api/chat/query"),          # 對的路徑、錯的 method
        ("POST", "/api/profiles"),           # 對的路徑、錯的 method
    ],
)
def test_admin_endpoints_not_kiosk(method, path):
    assert auth.is_kiosk_api(method, path) is False

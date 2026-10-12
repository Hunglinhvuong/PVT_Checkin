import pytest

from dashboard.passwords import (
    DashboardAuthConfigError,
    hash_password,
    parse_dashboard_users,
    verify_password,
)


def test_password_hash_verifies_only_matching_password():
    encoded = hash_password("a-long-dashboard-password")
    assert verify_password("a-long-dashboard-password", encoded)
    assert not verify_password("wrong-password", encoded)


def test_password_hash_rejects_malformed_values():
    assert not verify_password("password", "plain-text")
    assert not verify_password("password", "pbkdf2_sha256$1$deadbeef$deadbeef")


def test_password_hash_requires_minimum_length():
    with pytest.raises(ValueError):
        hash_password("too-short")


def test_parse_dashboard_users_validates_accounts_and_duplicates():
    password_hash = hash_password("strong-password")
    users = parse_dashboard_users([
        {"username": "admin", "password_hash": password_hash, "telegram_id": 123},
    ])
    assert (users[0].username, users[0].telegram_id) == ("admin", 123)

    with pytest.raises(DashboardAuthConfigError):
        parse_dashboard_users([
            {"username": "admin", "password_hash": password_hash, "telegram_id": 123},
            {"username": "admin", "password_hash": password_hash, "telegram_id": 456},
        ])


def test_parse_dashboard_users_rejects_invalid_identity_or_hash():
    with pytest.raises(DashboardAuthConfigError):
        parse_dashboard_users([{"username": "admin", "password_hash": "plain", "telegram_id": 123}])
    with pytest.raises(DashboardAuthConfigError):
        parse_dashboard_users([
            {"username": "admin", "password_hash": hash_password("strong-password"), "telegram_id": 0},
        ])

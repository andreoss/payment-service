from app.config import Settings

# Check the declared field default rather than instantiating Settings(),
# since the latter picks up whatever WEBHOOK_ALLOW_PRIVATE_HOSTS /
# DEBUG_ENDPOINTS_ENABLED are set to in the current process environment
# (both are intentionally enabled for local/dev via .env).


def test_debug_endpoints_disabled_by_default():
    assert Settings.model_fields["debug_endpoints_enabled"].default is False


def test_private_webhook_hosts_disallowed_by_default():
    assert Settings.model_fields["webhook_allow_private_hosts"].default is False

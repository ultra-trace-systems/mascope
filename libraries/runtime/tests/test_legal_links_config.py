"""
Tests for the legal and support links in `[meta]`.

The web app renders them on the sign-in screen and in the About tab, so two
properties matter. A deployment that says nothing links Ultra Trace's published
privacy notice and support address - the documents live outside the repository,
which is why these are settings at all. And whatever an operator writes ends up
in an ``href``, where a ``javascript:`` URL would run in the app's own origin,
so only web URLs (and, for support, a mail address) get through.
"""

import pytest
from pydantic import ValidationError

from mascope_runtime.config import MetaConfig


LINKS = ["privacy_notice_url", "terms_url", "support_url"]


def test_defaults_link_the_published_privacy_notice_and_support():
    meta = MetaConfig()
    assert meta.privacy_notice_url == "https://ultratrace.eu/mascope/privacy"
    assert meta.support_url == "mailto:support@ultratrace.eu"
    # No terms of service are published yet, so that link stays hidden.
    assert meta.terms_url == ""


def test_the_links_reach_the_web_app():
    """The frontend reads them from the serialized `[meta]`, so they have to
    survive `model_dump()` - a field pydantic dropped would silently fall back
    to the frontend's own default and ignore the operator."""
    dumped = MetaConfig(terms_url="https://example.org/terms").model_dump()
    assert dumped["terms_url"] == "https://example.org/terms"
    assert dumped["privacy_notice_url"] == "https://ultratrace.eu/mascope/privacy"
    assert dumped["support_url"] == "mailto:support@ultratrace.eu"


@pytest.mark.parametrize("field", LINKS)
def test_an_empty_value_hides_the_link(field):
    assert getattr(MetaConfig(**{field: ""}), field) == ""
    assert getattr(MetaConfig(**{field: "   "}), field) == ""


def test_surrounding_whitespace_is_dropped():
    meta = MetaConfig(terms_url="  https://example.org/terms \n")
    assert meta.terms_url == "https://example.org/terms"


@pytest.mark.parametrize("field", LINKS)
@pytest.mark.parametrize(
    "url", ["https://example.org/privacy", "http://intranet.local/privacy"]
)
def test_web_urls_are_accepted(field, url):
    assert getattr(MetaConfig(**{field: url}), field) == url


@pytest.mark.parametrize("field", LINKS)
@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "/privacy",
        "example.org/privacy",
        "ftp://example.org/privacy",
    ],
)
def test_anything_else_is_refused_naming_the_setting(field, url):
    with pytest.raises(ValidationError, match=field):
        MetaConfig(**{field: url})


@pytest.mark.parametrize("field", LINKS)
@pytest.mark.parametrize(
    "url",
    [
        "https://",
        "https:///privacy",
        "https://exa mple.org/privacy",
        "https://example.org/privacy\tnotice",
        "http://[bad/privacy",
        "https://example.org:port/privacy",
        "https://example.org/100%/privacy",
    ],
)
def test_a_link_the_browser_could_not_use_is_refused(field, url):
    """The right scheme is not enough: the web app hides a link its URL parser
    rejects, so without this the link would vanish with nothing saying why."""
    with pytest.raises(ValidationError, match=f"{field} is not a usable link"):
        MetaConfig(**{field: url})


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/privacy%20notice",
        "http://intranet.local:8080/privacy?lang=fi#data",
        "https://[2001:db8::1]/privacy",
    ],
)
def test_escaped_ported_and_ipv6_links_are_accepted(url):
    assert MetaConfig(privacy_notice_url=url).privacy_notice_url == url


@pytest.mark.parametrize(
    "url", ["mailto:", "mailto:?subject=Help", "mailto:help%zz@example.org"]
)
def test_a_mail_link_needs_a_decodable_address(url):
    with pytest.raises(ValidationError, match="support_url is not a usable link"):
        MetaConfig(support_url=url)


def test_support_may_be_a_mail_address():
    meta = MetaConfig(support_url="mailto:help@example.org")
    assert meta.support_url == "mailto:help@example.org"
    with_subject = "mailto:help@example.org?subject=Mascope%20support"
    assert MetaConfig(support_url=with_subject).support_url == with_subject


@pytest.mark.parametrize("field", ["privacy_notice_url", "terms_url"])
def test_documents_may_not_be_a_mail_address(field):
    with pytest.raises(ValidationError, match=field):
        MetaConfig(**{field: "mailto:legal@example.org"})

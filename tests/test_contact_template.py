"""Regression tests for B-SEC-2: visitor input is escaped in the contact email."""

from src.templates.contact_template import contact_email_template


def test_message_html_is_escaped():
    _, html = contact_email_template.generate_email_template(
        name="<b>Mallory</b>",
        email="mallory@example.com",
        message='<a href="https://evil.example">Verify your account</a>',
    )

    assert '<a href="https://evil.example">' not in html
    assert "&lt;a href=&quot;https://evil.example&quot;&gt;" in html
    assert "<b>Mallory</b>" not in html
    assert "&lt;b&gt;Mallory&lt;/b&gt;" in html


def test_subject_has_no_line_breaks():
    subject, _ = contact_email_template.generate_email_template(
        name="Mallory\r\nBcc: victim@example.com",
        email="mallory@example.com",
        message="hello",
    )

    assert "\r" not in subject
    assert "\n" not in subject


def test_reply_link_cannot_break_out_of_href():
    _, html = contact_email_template.generate_email_template(
        name="Mallory",
        email='mallory@example.com" onclick="alert(1)',
        message="hello",
    )

    assert 'onclick="alert(1)' not in html
    assert 'href="mailto:mallory@example.com' in html

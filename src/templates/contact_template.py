from datetime import UTC, datetime
from html import escape
from urllib.parse import quote

from src.core.config import settings


def _single_line(value: str) -> str:
    """Collapse CR/LF so user input can't inject email headers."""
    return " ".join(value.splitlines()).strip()


def _avatar_html() -> str:
    """The header image, or nothing when EMAIL_AVATAR_URL is unset."""
    url = settings.email_avatar_url.strip()
    if not url:
        return ""
    return f"""<img src="{escape(url, quote=True)}" width="84" height="84" alt="Aashish Regmi"
style="
border-radius:50%;
border:3px solid #A855F7;
object-fit:cover;
display:block;
margin:0 auto 20px;
"/>"""


class ContactEmailTemplate:
    PORTFOLIO_URL = settings.portfolio_url
    GITHUB_URL = settings.github_url
    LINKEDIN_URL = settings.linkedin_url

    @staticmethod
    def generate_email_template(name: str, email: str, message: str) -> tuple[str, str]:
        subject = f"🚀 New Portfolio Contact | {_single_line(name)[:100]}"
        reply_href = escape(f"mailto:{quote(_single_line(email), safe='@.+-_')}")
        # All visitor input is escaped before it is placed in the HTML body.
        name, email, message = escape(name), escape(email), escape(message)

        received_on = datetime.now(UTC).strftime("%B %d, %Y, %H:%M UTC")
        avatar = _avatar_html()

        html = f"""
        <!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Portfolio Contact</title>
</head>

<body style="
    margin:0;
    padding:40px 0;
    background:#0B0912;
    font-family:Arial, Helvetica, sans-serif;
">

<table width="100%" cellpadding="0" cellspacing="0">
<tr>
<td align="center">

<table width="650" cellpadding="0" cellspacing="0"
style="
background:#121019;
border:1px solid #2E2545;
border-radius:18px;
overflow:hidden;
">

<!-- HEADER -->

<tr>
<td
style="
padding:45px;
background:linear-gradient(135deg,#2A1B4A,#171321 55%,#0F0D15);
text-align:center;
">

{avatar}

<h3 style="
margin:0;
font-size:15px;
font-weight:400;
letter-spacing:6px;
color:#B28CFF;
">
HELLO! I'M
</h3>

<h1 style="
margin:15px 0 5px;
font-size:42px;
color:#FFFFFF;
font-weight:900;
letter-spacing:2px;
">
AASHISH REGMI
</h1>

<p style="
margin:0;
font-size:18px;
color:#CFCFCF;
">
Backend Engineer • Python • Django • FastAPI
</p>

</td>
</tr>

<!-- TITLE -->

<tr>
<td style="padding:35px 40px 10px;">

<h2 style="
margin:0;
font-size:28px;
color:#A855F7;
">
🚀 New Portfolio Contact
</h2>

<p style="
color:#A6A6A6;
font-size:15px;
margin-top:12px;
line-height:1.8;
">
{name} has submitted your portfolio contact form.
</p>

<p style="
margin-top:10px;
font-size:13px;
color:#7C7C7C;
">
🕒 Received on {received_on}
</p>

</td>
</tr>

<!-- DETAILS -->

<tr>
<td style="padding:10px 40px 40px;">

<table
width="100%"
cellpadding="18"
cellspacing="0"
style="
background:#191623;
border:1px solid #312A48;
border-radius:14px;
">

<tr>

<td>

<p style="
margin:0;
font-size:13px;
color:#8F8F8F;
text-transform:uppercase;
letter-spacing:2px;
">
👤 Name
</p>

<p style="
margin:8px 0 25px;
font-size:20px;
font-weight:bold;
color:white;
">
{name}
</p>

<p style="
margin:0;
font-size:13px;
color:#8F8F8F;
text-transform:uppercase;
letter-spacing:2px;
">
📧 Email
</p>

<p style="
margin:8px 0 25px;
font-size:18px;
color:#C9B7FF;
">
{email}
</p>

<p style="
margin:0;
font-size:13px;
color:#8F8F8F;
text-transform:uppercase;
letter-spacing:2px;
">
💬 Message
</p>

<div style="
margin-top:12px;
padding:20px;
background:#0F0D15;
border-left:4px solid #A855F7;
border-radius:8px;
color:#ECECEC;
font-size:15px;
line-height:1.8;
white-space:pre-wrap;
">

{message}

</div>

</td>

</tr>

</table>

<!-- ACTION BUTTONS -->

<table width="100%" cellpadding="0" cellspacing="0" style="margin-top:30px;">
<tr>
<td align="center" style="padding:0 8px;">
<a href="{reply_href}" target="_blank" style="
display:block;
background:linear-gradient(135deg,#A855F7,#7C3AED);
color:#FFFFFF;
text-decoration:none;
font-size:15px;
font-weight:bold;
padding:14px 0;
border-radius:10px;
text-align:center;
">
✉️ Reply to Visitor
</a>
</td>
<td align="center" style="padding:0 8px;">
<a href="{ContactEmailTemplate.PORTFOLIO_URL}" target="_blank" style="
display:block;
background:transparent;
color:#C9B7FF;
text-decoration:none;
font-size:15px;
font-weight:bold;
padding:12px 0;
border-radius:10px;
border:1px solid #4B3A73;
text-align:center;
">
🌐 View Portfolio
</a>
</td>
</tr>
</table>

</td>
</tr>

<!-- FOOTER -->

<tr>

<td
style="
padding:35px;
text-align:center;
background:#0F0D15;
border-top:1px solid #2B2340;
">

<p style="
margin:0;
font-size:18px;
font-weight:bold;
color:#FFFFFF;
">
Aashish Regmi
</p>

<p style="
margin:10px 0 0;
color:#A6A6A6;
font-size:14px;
">
Backend Engineer • Portfolio Contact System
</p>

<p style="margin:20px 0 0;">
<a href="{ContactEmailTemplate.GITHUB_URL}" target="_blank" style="
color:#C9B7FF;
text-decoration:none;
font-size:13px;
margin:0 12px;
">
GitHub
</a>
<span style="color:#3A3350;">•</span>
<a href="{ContactEmailTemplate.LINKEDIN_URL}" target="_blank" style="
color:#C9B7FF;
text-decoration:none;
font-size:13px;
margin:0 12px;
">
LinkedIn
</a>
</p>

<p style="
margin:18px 0 0;
font-size:12px;
color:#6F6F6F;
">
This email was automatically generated from your portfolio website.
</p>

</td>

</tr>

</table>

</td>
</tr>

</table>

</body>
</html>
                """
        return subject, html


contact_email_template = ContactEmailTemplate()


def confirmation_email_template(name: str) -> tuple[str, str]:
    """A short "message received" note for the visitor. Never echoes their message."""
    first_name = escape(_single_line(name).split(" ")[0][:50]) or "there"
    subject = "Thanks for getting in touch"
    html = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:24px;font-family:Arial,Helvetica,sans-serif;color:#0F1E33;">
<p>Hi {first_name},</p>
<p>Thanks for your message. It reached me, and I'll reply within a couple of working days.</p>
<p>Aashish Regmi<br><a href="{escape(settings.portfolio_url, quote=True)}">{escape(settings.portfolio_url)}</a></p>
<p style="color:#475569;font-size:12px;">You're receiving this because this address was entered
in the contact form on my portfolio. If that wasn't you, you can ignore this email.</p>
</body></html>"""
    return subject, html

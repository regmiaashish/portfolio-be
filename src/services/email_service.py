import smtplib
from email.message import EmailMessage
from typing import Protocol

from src.core.config import settings

SMTP_TIMEOUT_SECONDS = 15


class EmailSender(Protocol):
    def send_email(self, recipient: str, subject: str, html: str) -> None: ...


class EmailService:
    """Sends HTML email over SMTP with STARTTLS. Blocking: call it via asyncio.to_thread."""

    def __init__(self):
        self.smtp_host = settings.smtp_host
        self.smtp_port = settings.smtp_port
        self.smtp_user = settings.smtp_user
        self.smtp_password = settings.smtp_password.get_secret_value()

    def send_email(self, recipient: str, subject: str, html: str) -> None:
        message = EmailMessage()
        message["From"] = self.smtp_user
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(
            "This email contains HTML content. Please view it in an email client that supports HTML."
        )
        message.add_alternative(html, subtype="html")

        # The timeout stops a hung SMTP server from tying up a worker thread forever.
        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
            smtp.starttls()
            smtp.login(self.smtp_user, self.smtp_password)
            smtp.send_message(message)


email_service = EmailService()

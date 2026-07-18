import smtplib
from email.message import EmailMessage

from src.core.config import settings
from src.templates.contact_template import contact_email_template


class EmailService:
    def __init__(self):
        self.smtp_host = settings.smtp_host
        self.smtp_port = settings.smtp_port
        self.smtp_user = settings.smtp_user
        self.smtp_password = settings.smtp_password

    def send_email(
        self,
        recipient: str,
        subject: str,
        html: str,
    ) -> None:
        """
        Send a plain text email.
        """

        # Create email message
        message = EmailMessage()

        message["From"] = self.smtp_user
        message["To"] = recipient
        message["Subject"] = subject

        message.set_content("This email contains HTML content. Please view it in an email client that supports HTML.")
        message.add_alternative(html, subtype="html")

        # Connect to SMTP server
        with smtplib.SMTP(
            self.smtp_host,
            self.smtp_port,
        ) as smtp:

            # Upgrade to encrypted connection
            smtp.starttls()

            # Authenticate
            smtp.login(
                self.smtp_user,
                self.smtp_password,
            )

            # Send email
            smtp.send_message(message)


email_service = EmailService()
from src.core.config import settings
from src.services.email_service import email_service
from src.templates.contact_template import contact_email_template


class ContactService:

    def __init__(self):
        self.email_service = email_service

    def send_contact_email(self, name: str, email: str, message: str) -> None:
        """
        Send a contact email using the EmailService.
        """
        subject, html = contact_email_template.generate_email_template(name=name, email=email, message=message)

        self.email_service.send_email(
                recipient=settings.super_admin_email,
                subject=subject,
                html=html
            )

contact_service = ContactService()




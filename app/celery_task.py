from celery import Celery

from app.core.mail import send_email, create_message

c_app = Celery("pos")

c_app.config_from_object("app.core.config")


@c_app.task
def send_email_task(recipients: list[str], subject: str, body: str):
    message = create_message(recipients=recipients, subject=subject, body=body)
    send_email(message)
    print("Email sent")

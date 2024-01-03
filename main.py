import base64
import datetime
import mimetypes
import os.path
from email.message import EmailMessage
from io import BytesIO
from typing import Dict, Generator

from PIL import Image
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# If modifying these scopes, delete the file token.json.
# see also https://developers.google.com/identity/protocols/oauth2/scopes#gmail
# SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
SCOPES = ["https://mail.google.com/"]

# the bot's address
MY_ADDRESS = os.environ["EMAIL_ADDR_PDF_BOT"]
# the script maintainer
MAINTAINER_ADDRESS = os.environ["EMAIL_ADDR_PDF_MAINTAINER"]
# whitelisted emails. Emails from any non-whitelisted addresses are ignored.
ENV_WHITELIST = os.environ.get("EMAIL_ADDRS_PDF_WHITELIST")

# TODO: add support for Google Drive links

# "me" is a special token
USER_ID = "me"

FROM_WHITELIST = [MY_ADDRESS, MAINTAINER_ADDRESS]
if ENV_WHITELIST:
    FROM_WHITELIST.append(ENV_WHITELIST)
else:
    # this is fine if testing
    print("Not all whitelisted emails were set in the environment variables")


def image_to_pdf_bytes(image_bytes: BytesIO) -> BytesIO:
    image = Image.open(image_bytes)
    buffer = BytesIO()
    image.save(buffer, "PDF", resolution=100.0)
    return buffer


def create_email_send_pdfs(service,
                           target_address: str,
                           attachments: Dict[str, BytesIO]):
    # https://developers.google.com/gmail/api/guides/sending#python
    mime_message = EmailMessage()
    mime_message.set_content("Your converted images are attached as PDFs. This is an automated email. "
                             "This inbox is not regularly monitored. For questions or problems, please contact "
                             f"the maintainer at {MAINTAINER_ADDRESS}")
    mime_message["To"] = target_address
    mime_message["From"] = MY_ADDRESS
    mime_message["Subject"] = "Your images as PDFs"

    for name, attachment_data in attachments.items():
        attachment_data.seek(0)
        binary_data = attachment_data.read()
        maintype, _, subtype = (mimetypes.guess_type(name)[0] or 'application/octet-stream').partition("/")
        mime_message.add_attachment(binary_data, maintype=maintype, subtype=subtype, filename=name)

    encoded_message = base64.urlsafe_b64encode(mime_message.as_bytes()).decode()

    send_message = (
        service.users()
        .messages()
        .send(userId=USER_ID, body={"raw": encoded_message})
        .execute()
    )
    return send_message


def list_messages(service, user_id=USER_ID, query=''):
    try:
        response = service.users().messages().list(userId=user_id, q=query).execute()
        messages = []
        if 'messages' in response:
            messages.extend(response['messages'])

        while 'nextPageToken' in response:
            page_token = response['nextPageToken']
            response = service.users().messages().list(userId=user_id, q=query, pageToken=page_token).execute()
            messages.extend(response['messages'])

        return messages
    except Exception as e:
        print(f"An error occurred: {e}")
        return []


def get_email(service, msg_id):
    # retrieve a specific email by its ID
    try:
        message = service.users().messages().get(userId=USER_ID, id=msg_id).execute()
        return message
    except Exception as e:
        print(f"An error occurred: {e}")
        return None


def respond_to_emails(service):
    emails = list_messages(service=service)
    # as of 2024-01-02 we get id and threadId keys
    email_ids = {email["id"] for email in emails}

    for email_id in email_ids:
        email = get_email(service=service, msg_id=email_id)
        raw_sender = next((header['value'] for header in email['payload']['headers'] if header['name'] == 'From'), None)
        subject = next((header['value'] for header in email['payload']['headers'] if header['name'] == 'Subject'), None)
        # example: 'Tue, 2 Jan 2024 06:45:33 -0800'
        date_str = next((header['value'] for header in email['payload']['headers'] if header['name'] == 'Date'), None)

        # general note: attachment 0 is the body
        all_attachments = email['payload']['parts']
        filtered_attachments = [attach_dct for attach_dct in all_attachments
                                if attach_dct['mimeType'] in ['application/pdf', 'image/jpeg', 'image/png']]

        # sender is like this: 'First Last <firstname_lastname@gmail.com>', or like this 'email@gmail.com'
        if "<" not in raw_sender:
            sender = raw_sender
        else:
            sender = raw_sender.split("<")[1][:-1]
        if sender not in FROM_WHITELIST:
            print(f"Email `{sender}` not in whitelist")
            continue

        date = datetime.datetime.strptime(date_str, '%a, %d %b %Y %H:%M:%S %z')
        age_in_seconds = (datetime.datetime.now(date.tzinfo) - date).seconds
        if age_in_seconds > (3600 * 3):
            # precaution: don't spam someone endlessly if our trashing logic is bad
            continue
        elif age_in_seconds > (3600 * 24 * 7):
            # clear out spam, and emails which failed to trigger a response from our bot
            print(f"Trashing email > 1 week old from sender {sender}, titled `{subject}`")
            service.users().messages().trash(userId=USER_ID, id=email_id).execute()

        if not filtered_attachments:
            continue

        # convert
        pdfs: Dict[str, BytesIO] = {}
        generate_fname = yield_filename()
        for attachment_dct in filtered_attachments:
            attachment_id = attachment_dct['body']['attachmentId']
            attachment = service.users().messages().attachments().get(userId=USER_ID, messageId=email_id,
                                                                      id=attachment_id).execute()
            data_as_bytes = BytesIO(base64.urlsafe_b64decode(attachment['data'].encode('UTF-8')))
            if attachment_dct['mimeType'] == 'application/pdf':
                pdfs[next(generate_fname)] = data_as_bytes
            else:
                pdfs[next(generate_fname)] = image_to_pdf_bytes(data_as_bytes)

        print(f"Sending email to `{sender}`")
        create_email_send_pdfs(service=service, target_address=sender, attachments=pdfs)
        service.users().messages().trash(userId=USER_ID, id=email_id).execute()


def yield_filename(base_name: str = "pdf", extension="pdf") -> Generator[str, None, None]:
    counter = 1
    while True:
        yield f"{base_name}_{counter}.{extension}"
        counter += 1


def get_gmail_service():
    creds = None
    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # got this here https://console.cloud.google.com/apis/credentials?project=email-service-1-410012
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    service = build("gmail", "v1", credentials=creds)
    return service


def main():
    try:
        # Call the Gmail API
        service = get_gmail_service()
        respond_to_emails(service=service)
    except HttpError as error:
        print(f"An error occurred: {error}")
        return


if __name__ == "__main__":
    main()

from __future__ import print_function
import os.path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# If modifying scopes, delete token.json and re-run.
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

def main():
    creds = None
    # If token.json already exists, load it
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    # If no valid creds, run login flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Use credentials.json (OAuth client secrets) for first-time auth
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=0)
        # Save the creds for the next run
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    # Quick test: list 10 files
    service = build("drive", "v3", credentials=creds)
    results = service.files().list(
        pageSize=10, fields="files(id, name)"
    ).execute()
    items = results.get("files", [])

    if not items:
        print("No files found.")
    else:
        print("Files:")
        for item in items:
            print(f"{item['name']} ({item['id']})")

if __name__ == "__main__":
    main()

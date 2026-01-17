from flask import Flask, redirect, request, session, url_for
from flask import render_template
from dotenv import load_dotenv
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from datetime import datetime, timedelta
import json
import os

# Load environment variables from .env
load_dotenv()

# Allow HTTP for local OAuth testing ONLY
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# Create Flask app
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret")

# Home route
def get_google_flow():
    return Flow.from_client_config(
        {
            "web": {
                "client_id": os.getenv("GOOGLE_CLIENT_ID"),
                "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [os.getenv("GOOGLE_REDIRECT_URI")]
            }
        },
        scopes=["https://www.googleapis.com/auth/gmail.modify"],
        redirect_uri=os.getenv("GOOGLE_REDIRECT_URI")
    )

def get_gmail_service():
    creds_data = session.get("credentials")

    if not creds_data:
        return None

    creds = Credentials(
        token=creds_data["token"],
        refresh_token=creds_data["refresh_token"],
        token_uri=creds_data["token_uri"],
        client_id=creds_data["client_id"],
        client_secret=creds_data["client_secret"],
        scopes=creds_data["scopes"],
    )

    return build("gmail", "v1", credentials=creds)

def get_emails_by_label(label_id, days_old=30):
    service = get_gmail_service()

    if not service:
        return "Not logged in. Go to /login first."

    cutoff_date = datetime.utcnow() - timedelta(days=days_old)
    formatted_date = cutoff_date.strftime("%Y/%m/%d")
    query = f"before:{formatted_date}"

    messages = []
    page_token = None

    while True:
        response = service.users().messages().list(
            userId="me",
            labelIds=[label_id],
            q=query,
            maxResults=100,
            pageToken=page_token
        ).execute()

        messages.extend(response.get("messages", []))
        page_token = response.get("nextPageToken")

        if not page_token:
            break

    if not messages:
        return f"No emails older than {days_old} days for label: {label_id}"

    output = []

    for msg in messages:
        msg_data = service.users().messages().get(
            userId="me",
            id=msg["id"],
            format="metadata",
            metadataHeaders=["Subject", "From", "Date"]
        ).execute()

        headers = msg_data["payload"]["headers"]
        email_info = {h["name"]: h["value"] for h in headers}

        output.append(
            f"<b>Subject:</b> {email_info.get('Subject')}<br>"
            f"<b>From:</b> {email_info.get('From')}<br>"
            f"<b>Date:</b> {email_info.get('Date')}<br><br>"
        )

    return "".join(output)

def trash_emails_by_label(label_id, days_old=30):
    service = get_gmail_service()

    if not service:
        return {
            "status": "error",
            "message": "Not logged in"
        }

    cutoff_date = datetime.utcnow() - timedelta(days=days_old)
    formatted_date = cutoff_date.strftime("%Y/%m/%d")
    query = f"before:{formatted_date}"

    messages = []
    page_token = None

    while True:
        response = service.users().messages().list(
            userId="me",
            labelIds=[label_id],
            q=query,
            maxResults=100,
            pageToken=page_token
        ).execute()

        messages.extend(response.get("messages", []))
        page_token = response.get("nextPageToken")

        if not page_token:
            break

    scanned = len(messages)

    if scanned == 0:
        return {
            "status": "ok",
            "scanned": 0,
            "deleted": 0,
            "message": f"No emails older than {days_old} days"
        }

    for msg in messages:
        service.users().messages().trash(
            userId="me",
            id=msg["id"]
        ).execute()

    return {
        "status": "ok",
        "scanned": scanned,
        "deleted": scanned,
        "message": f"Deleted {scanned} emails successfully"
    }

def get_threads_by_label(label_id, days_older_than=30):
    service = get_gmail_service()

    if not service:
        return []

    cutoff_date = datetime.utcnow() - timedelta(days=days_older_than)
    formatted_date = cutoff_date.strftime("%Y/%m/%d")
    query = f"before:{formatted_date}"

    threads = []
    page_token = None

    while True:
        response = service.users().threads().list(
            userId="me",
            labelIds=[label_id],
            q=query,
            maxResults=100,
            pageToken=page_token
        ).execute()

        threads.extend(response.get("threads", []))
        page_token = response.get("nextPageToken")

        if not page_token:
            break

    return threads

def dry_run_emails_by_label(label_id, days_old=30):
    threads = get_threads_by_label(label_id, days_older_than=days_old)
    return f"🧪 Dry run: {len(threads)} threads would be moved to Trash"

@app.route("/")
def home():
    return render_template("landing.html")

@app.route("/login")
def login():
    flow = get_google_flow()
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true"
    )
    session["state"] = state
    return redirect(authorization_url)

@app.route("/callback")
def callback():
    flow = get_google_flow()
    flow.fetch_token(authorization_response=request.url)

    credentials = flow.credentials
    session["credentials"] = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes
    }

    return redirect(url_for("dashboard"))

@app.route("/dashboard")
def dashboard():

    history = load_history()

    total_deleted = sum(item["deleted"] for item in history)

    last_cleanup = history[-1]["date"] if history else "Never"

    status = "Healthy" if total_deleted > 0 else "Idle"

    return render_template(
        "dashboard.html",
        total_deleted=total_deleted,
        last_cleanup=last_cleanup,
        status=status,
        history=history[::-1]  # latest first
    )

@app.route("/emails/spam")
def spam_emails():
    return get_emails_by_label("SPAM")

@app.route("/emails/promotions")
def promotions_emails():
    return get_emails_by_label("CATEGORY_PROMOTIONS")

@app.route("/emails/social")
def social_emails():
    return get_emails_by_label("CATEGORY_SOCIAL")

@app.route("/emails/spam/older/<int:days>")
def spam_emails_older(days):
    return get_emails_by_label("SPAM", days_old=days)

@app.route("/emails/promotions/older/<int:days>")
def promotions_emails_older(days):
    return get_emails_by_label("CATEGORY_PROMOTIONS", days_old=days)

@app.route("/emails/social/older/<int:days>")
def social_emails_older(days):
    return get_emails_by_label("CATEGORY_SOCIAL", days_old=days)

@app.route("/dryrun/<label>")
def dryrun_cleanup(label):
    label_map = {
        "spam": "SPAM",
        "promotions": "CATEGORY_PROMOTIONS",
        "social": "CATEGORY_SOCIAL"
    }

    if label not in label_map:
        return "Invalid label"

    threads = get_threads_by_label(label_map[label])

    return f"🧪 Dry run: {len(threads)} threads would be deleted from {label.upper()}"

@app.route("/dry-run/spam")
def dry_run_spam():
    return dry_run_emails_by_label("SPAM")

@app.route("/dry-run/promotions")
def dry_run_promotions():
    return dry_run_emails_by_label("CATEGORY_PROMOTIONS")

@app.route("/dry-run/social")
def dry_run_social():
    return dry_run_emails_by_label("CATEGORY_SOCIAL")

@app.route("/cleanup/<label>")
def cleanup(label):

    days = int(request.args.get("days", 30))

    label_map = {
        "spam": "SPAM",
        "promotions": "CATEGORY_PROMOTIONS",
        "social": "CATEGORY_SOCIAL"
    }

    if label not in label_map:
        return "Invalid label"

    result = trash_emails_by_label(label_map[label], days)

    scanned = result.get("scanned", 0)
    deleted = result.get("deleted", 0)
    message = result.get("message")

    history = load_history()

    history.append({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "days": days,
        "deleted": deleted,
        "scanned": scanned,
        "status": "Success" if deleted > 0 else "Clean"
    })

    save_history(history)

    return render_template(
        "result.html",
        title="Cleanup Completed 🎉",
        message=message,
        scanned=scanned,
        deleted=deleted
    )

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")

def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    with open(HISTORY_FILE, "r") as f:
        return json.load(f)

def save_history(data):
    with open(HISTORY_FILE, "w") as f:
        json.dump(data, f, indent=4)

# Run the app
if __name__ == "__main__":
    app.run(debug=True, port=5001)


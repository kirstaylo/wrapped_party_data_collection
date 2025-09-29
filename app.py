import os
import json
import base64
import requests
import pandas as pd
from flask import Flask, redirect, request, session, url_for, render_template
import spotipy
from dotenv import load_dotenv

# Google Drive imports (user OAuth)
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Load environment variables
load_dotenv()

# Flask app
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_secret_key")

# Spotify credentials
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:5000/callback")
SCOPE = "user-top-read"

# Google Drive setup
drive_service = None
FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID")


# ------------------------
# Google Drive Helpers
# ------------------------
def init_drive_service():
    """Load Drive API client using GOOGLE_TOKEN env var or token.json (local dev)."""
    global drive_service
    try:
        creds = None
        google_token = os.getenv("GOOGLE_TOKEN")
        if google_token:
            creds = Credentials.from_authorized_user_info(
                json.loads(google_token),
                ["https://www.googleapis.com/auth/drive.file"]
            )
            print("✅ Google Drive via GOOGLE_TOKEN")
        elif os.path.exists("token.json"):
            creds = Credentials.from_authorized_user_file(
                "token.json", ["https://www.googleapis.com/auth/drive.file"]
            )
            print("✅ Google Drive via token.json")
        else:
            print("⚠️ No Google credentials found.")

        if creds:
            drive_service = build("drive", "v3", credentials=creds)
    except Exception as e:
        print(f"⚠️ Drive init failed: {e}")
        drive_service = None


init_drive_service()


def get_or_create_user_folder(custom_name: str) -> str:
    """Return a folder ID inside FOLDER_ID, creating it if necessary."""
    if not drive_service:
        return ""

    query = f"name='{custom_name}' and mimeType='application/vnd.google-apps.folder'"
    if FOLDER_ID:
        query += f" and '{FOLDER_ID}' in parents"

    results = drive_service.files().list(
        q=query,
        spaces="drive",
        fields="files(id, name, parents)",
        pageSize=5,
        includeItemsFromAllDrives=True,
        supportsAllDrives=True,
    ).execute()

    folders = results.get("files", [])
    if folders:
        folder_id = folders[0]["id"]
        print(f"📂 Found folder {custom_name}: {folder_id}")
        return folder_id

    metadata = {"name": custom_name, "mimeType": "application/vnd.google-apps.folder"}
    if FOLDER_ID:
        metadata["parents"] = [FOLDER_ID]

    folder = drive_service.files().create(
        body=metadata, fields="id, parents", supportsAllDrives=True
    ).execute()
    folder_id = folder["id"]
    print(f"📁 Created folder {custom_name}: {folder_id}")
    return folder_id


def upload_to_drive(filepath: str, filename: str, parent_id: str) -> str:
    """Upload a file to Drive under the given parent folder."""
    if not drive_service:
        return ""

    if not parent_id:
        raise RuntimeError("🚨 upload_to_drive called without parent_id!")

    metadata = {"name": filename, "parents": [parent_id]}
    media = MediaFileUpload(filepath, mimetype="text/csv")

    uploaded = drive_service.files().create(
        body=metadata,
        media_body=media,
        fields="id, parents",
        supportsAllDrives=True,
    ).execute()

    print(
        f"📤 Uploaded {filename} → requested parent {parent_id}, got parents {uploaded.get('parents')}"
    )
    return uploaded["id"]


def save_and_upload(df: pd.DataFrame, filepath: str, filename: str, parent_id: str):
    """Save CSV locally and upload to Drive."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    df.to_csv(filepath, index=False)
    print(f"✅ Saved {len(df)} rows → {filepath}")

    try:
        upload_to_drive(filepath, filename, parent_id)
    except Exception as e:
        print(f"⚠️ Upload failed for {filename}: {e}")


# ------------------------
# Spotify Data Helpers
# ------------------------
def save_all_user_data(sp: spotipy.Spotify, spotify_username: str, custom_name: str):
    """Fetch top artists/tracks and save to Drive under custom_name folder."""
    user_dir = os.path.join("data", spotify_username)
    os.makedirs(user_dir, exist_ok=True)

    def get_top_tracks(time_range: str, total_limit: int = 100):
        results, fetched = [], 0
        while fetched < total_limit:
            batch = sp.current_user_top_tracks(
                limit=min(50, total_limit - fetched),
                offset=fetched,
                time_range=time_range,
            ).get("items", [])
            if not batch:
                break
            results.extend(batch)
            fetched += len(batch)
        return results

    user_folder_id = get_or_create_user_folder(custom_name)
    time_ranges = ["short_term", "medium_term", "long_term"]

    for tr in time_ranges:
        print(f"🔄 Collecting data: {tr}")
        range_dir = os.path.join(user_dir, tr)
        os.makedirs(range_dir, exist_ok=True)

        # Artists
        artists = sp.current_user_top_artists(limit=20, time_range=tr).get("items", [])
        if artists:
            artist_df = pd.DataFrame(
                [
                    {"Rank": i + 1, "Artist": a.get("name", ""), "ID": a.get("id", "")}
                    for i, a in enumerate(artists)
                ]
            )
            save_and_upload(
                artist_df,
                os.path.join(range_dir, "artists.csv"),
                f"{spotify_username}_{tr}_artists.csv",
                parent_id=user_folder_id,
            )

        # Tracks
        tracks = get_top_tracks(tr, 100)
        if tracks:
            track_df = pd.DataFrame(
                [
                    {
                        "Rank": i + 1,
                        "Track": t.get("name", ""),
                        "Artist": (t.get("artists") or [{}])[0].get("name", ""),
                        "ID": t.get("id", ""),
                    }
                    for i, t in enumerate(tracks)
                ]
            )
            save_and_upload(
                track_df,
                os.path.join(range_dir, "tracks.csv"),
                f"{spotify_username}_{tr}_tracks.csv",
                parent_id=user_folder_id,
            )


# ------------------------
# Flask Routes
# ------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/login", methods=["POST"])
def login():
    """Start Spotify OAuth."""
    session.clear()
    session["custom_name"] = request.form.get("custom_name", "Unknown_User")
    auth_url = (
        "https://accounts.spotify.com/authorize?"
        f"client_id={CLIENT_ID}&response_type=code&redirect_uri={REDIRECT_URI}"
        f"&scope={SCOPE}"
    )
    return redirect(auth_url)


@app.route("/callback")
def callback():
    """Spotify redirects here after user login."""
    code = request.args.get("code")
    if not code:
        return redirect(url_for("index"))

    # --- manual token exchange (no Spotipy cache) ---
    token_url = "https://accounts.spotify.com/api/token"
    payload = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI}
    basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    headers = {"Authorization": f"Basic {basic}"}

    resp = requests.post(token_url, data=payload, headers=headers, timeout=10)
    resp.raise_for_status()
    token_info = resp.json()

    session["spotify_token"] = token_info["access_token"]
    session["spotify_expires_in"] = token_info.get("expires_in")

    sp = spotipy.Spotify(auth=session["spotify_token"])
    profile = sp.current_user()
    print("✅ Logged in as:", profile.get("id"), "|", profile.get("display_name"))

    session["spotify_username"] = profile.get("id", "UnknownSpotifyUser")
    session["display_name"] = profile.get("display_name", session["spotify_username"])
    session["custom_name"] = session.get("custom_name", "Unknown_User")

    save_all_user_data(sp, session.get("display_name", "Unknown User"), session["custom_name"])
    return redirect(url_for("summary"))


@app.route("/summary")
def summary():
    """Show top artists/tracks in the UI."""
    spotify_token = session.get("spotify_token")
    if not spotify_token:
        return redirect(url_for("index"))

    sp = spotipy.Spotify(auth=spotify_token)
    display_name = session.get("display_name", "Unknown User")
    time_range = request.args.get("time_range", "medium_term")

    artists = sp.current_user_top_artists(limit=10, time_range=time_range).get("items", [])
    tracks = sp.current_user_top_tracks(limit=10, time_range=time_range).get("items", [])

    return render_template(
        "summary.html",
        display_name=display_name,
        artists=artists,
        tracks=tracks,
        time_range=time_range,
    )

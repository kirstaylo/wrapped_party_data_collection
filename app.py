import os
import pandas as pd
from flask import Flask, redirect, request, session, url_for, render_template
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
import json

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

def init_drive_service():
    """Load Drive API client using token.json (from OAuth flow) or GOOGLE_TOKEN env var."""
    global drive_service
    try:
        creds = None

        # 1️⃣ Prefer env var on Heroku
        google_token = os.getenv("GOOGLE_TOKEN")
        if google_token:
            creds = Credentials.from_authorized_user_info(
                json.loads(google_token),
                ["https://www.googleapis.com/auth/drive.file"]
            )
            print("✅ Google Drive authenticated via GOOGLE_TOKEN env var")

        # 2️⃣ Fallback to local token.json for dev
        elif os.path.exists("token.json"):
            creds = Credentials.from_authorized_user_file(
                "token.json", ["https://www.googleapis.com/auth/drive.file"]
            )
            print("✅ Google Drive authenticated via token.json")

        else:
            print("⚠️ No Google credentials found. Run drive_auth.py first.")

        if creds:
            drive_service = build("drive", "v3", credentials=creds)

    except Exception as e:
        print(f"⚠️ Failed to init Google Drive service: {e}")
        drive_service = None

init_drive_service()

def get_or_create_user_folder(custom_name: str) -> str:
    """Get or create a Drive folder for this user inside the parent folder."""
    if not drive_service:
        print("⚠️ Google Drive not available, skipping folder creation.")
        return ""

    query = f"name='{custom_name}' and mimeType='application/vnd.google-apps.folder'"
    if FOLDER_ID:
        query += f" and '{FOLDER_ID}' in parents"

    results = drive_service.files().list(
        q=query,
        spaces="drive",
        fields="files(id, name)",
        pageSize=1
    ).execute()

    folders = results.get("files", [])
    if folders:
        folder_id = folders[0]["id"]
        print(f"📂 Found existing folder for {custom_name}: {folder_id}")
        return folder_id

    # Create new folder
    metadata = {
        "name": custom_name,
        "mimeType": "application/vnd.google-apps.folder"
    }
    if FOLDER_ID:
        metadata["parents"] = [FOLDER_ID]

    folder = drive_service.files().create(body=metadata, fields="id").execute()
    folder_id = folder.get("id")
    print(f"📁 Created new folder for {custom_name}: {folder_id}")
    return folder_id

def upload_to_drive(filepath: str, filename: str, parent_id: str = None) -> str:
    """Upload CSV file to Google Drive inside the specified folder."""
    if not drive_service:
        print("⚠️ Google Drive not available. Skipping upload.")
        return ""

    file_metadata = {"name": filename}
    if parent_id:
        file_metadata["parents"] = [parent_id]

    media = MediaFileUpload(filepath, mimetype="text/csv")
    uploaded = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, parents"
    ).execute()

    print(f"📤 Uploaded {filename} → folder {parent_id} (id: {uploaded.get('id')})")
    return uploaded.get("id")

def save_and_upload(df: pd.DataFrame, filepath: str, filename: str, parent_id: str = None):
    """Save CSV locally and upload to Google Drive inside user folder."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    df.to_csv(filepath, index=False)
    print(f"✅ Saved {len(df)} rows to {filepath}")

    try:
        upload_to_drive(filepath, filename, parent_id=parent_id)
    except Exception as e:
        print(f"⚠️ Upload failed for {filename}: {e}")

def save_all_user_data(sp: spotipy.Spotify, spotify_username: str, custom_name: str):
    """Fetch full top data and save/upload CSVs (runs once after login)."""
    user_dir = os.path.join("data", spotify_username)
    os.makedirs(user_dir, exist_ok=True)

    def get_top_tracks(time_range: str, total_limit: int = 100):
        results, fetched = [], 0
        while fetched < total_limit:
            batch = sp.current_user_top_tracks(
                limit=min(50, total_limit - fetched),
                offset=fetched,
                time_range=time_range,
            )["items"]
            if not batch:
                break
            results.extend(batch)
            fetched += len(batch)
        return results

    time_ranges = ["short_term", "medium_term", "long_term"]
    user_folder_id = get_or_create_user_folder(custom_name)

    for tr in time_ranges:
        print(f"🔄 Collecting data for time range: {tr}")
        range_dir = os.path.join(user_dir, tr)
        os.makedirs(range_dir, exist_ok=True)

        # Top Artists
        artists = sp.current_user_top_artists(limit=20, time_range=tr).get("items", [])
        if artists:
            artist_df = pd.DataFrame([{
                "Rank": i + 1,
                "Artist": a.get("name", "Unknown Artist"),
                "ID": a.get("id", "")
            } for i, a in enumerate(artists)])
            save_and_upload(
                artist_df,
                os.path.join(range_dir, "artists.csv"),
                f"{spotify_username}_{tr}_artists.csv",
                parent_id=user_folder_id
            )

        # Top Tracks
        tracks = get_top_tracks(tr, total_limit=100) or []
        if tracks:
            track_df = pd.DataFrame([{
                "Rank": i + 1,
                "Track": t.get("name", "Unknown Track"),
                "Artist": (t.get("artists") or [{}])[0].get("name", "Unknown Artist"),
                "ID": t.get("id", "")
            } for i, t in enumerate(tracks)])
            save_and_upload(
                track_df,
                os.path.join(range_dir, "tracks.csv"),
                f"{spotify_username}_{tr}_tracks.csv",
                parent_id=user_folder_id
            )

@app.route("/")
def index():
    """Landing page with name input + login button."""
    return render_template("index.html")

@app.route("/login", methods=["POST"])
def login():
    """Save name and send user to Spotify auth page."""
    session.clear()
    session["custom_name"] = request.form.get("custom_name", "Unknown_User")
    sp_oauth = SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
    )
    return redirect(sp_oauth.get_authorize_url())

@app.route("/callback")
def callback():
    """Spotify redirects here after login approval and we save data once."""
    sp_oauth = SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
    )
    code = request.args.get("code")
    token_info = sp_oauth.get_access_token(code)
    sp = spotipy.Spotify(auth=token_info["access_token"])

    # ✅ Fetch Spotify profile
    profile = sp.current_user()
    spotify_username = profile.get("id", "UnknownSpotifyUser")
    display_name = profile.get("display_name", spotify_username)

    # Store only small values in session
    session["token_info"] = token_info
    session["spotify_username"] = spotify_username
    session["display_name"] = display_name
    session["custom_name"] = session.get("custom_name", "Unknown_User")

    # Save CSVs + upload once
    save_all_user_data(sp, spotify_username, session["custom_name"])

    return redirect(url_for("summary"))

@app.route("/summary")
def summary() -> str:
    """Fetch lightweight top artists/tracks for display (fresh API call)."""
    token_info = session.get("token_info")
    if not token_info:
        return redirect(url_for("index"))

    sp = spotipy.Spotify(auth=token_info["access_token"])
    spotify_username = session.get("spotify_username")
    display_name = session.get("display_name", spotify_username)

    time_range = request.args.get("time_range", "medium_term")

    # Fresh API call just for display
    artists = sp.current_user_top_artists(limit=10, time_range=time_range).get("items", [])
    tracks = sp.current_user_top_tracks(limit=10, time_range=time_range).get("items", [])

    return render_template(
        "summary.html",
        display_name=display_name,
        artists=artists,
        tracks=tracks,
        time_range=time_range,
    )

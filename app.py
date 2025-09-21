import os
import pandas as pd
from flask import Flask, redirect, request, session, url_for, render_template
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# Initialize the Flask app
app = Flask(__name__)
# Use a fixed secret key so sessions persist
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_secret_key")

# Spotify credentials
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:5000/callback")

# Requested scope
SCOPE = "user-top-read"


@app.route("/")
def index():
    """Landing page with name input + login button."""
    return render_template("index.html")


@app.route("/login", methods=["POST"])
def login():
    """Save name and send user to Spotify auth page."""
    # Store custom name in session
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
    """Spotify redirects here after login approval."""
    sp_oauth = SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
    )
    code = request.args.get("code")
    token_info = sp_oauth.get_access_token(code)
    session["token_info"] = token_info
    return redirect(url_for("summary"))


@app.route("/summary")
def summary() -> str:
    """Fetch + save top artists/tracks for all terms and render summary page."""

    # Require login
    token_info = session.get("token_info")
    if not token_info:
        return redirect(url_for("index"))

    sp = spotipy.Spotify(auth=token_info["access_token"])

    # Get display name from session
    custom_name: str = session.get("custom_name", "Unknown_User")

    # Base folder for this user
    user_dir = os.path.join("data", custom_name)
    os.makedirs(user_dir, exist_ok=True)

    # Helper: fetch up to 100 top tracks
    def get_top_tracks(sp: spotipy.Spotify, time_range: str, total_limit: int = 100):
        results = []
        fetched = 0
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

    # Collect for all time ranges
    time_ranges = ["short_term", "medium_term", "long_term"]
    all_data = {}

    for tr in time_ranges:
        print(f"🔄 Collecting data for time range: {tr}")

        # Make subfolders for each time range
        range_dir = os.path.join(user_dir, tr)
        os.makedirs(range_dir, exist_ok=True)

        # --- Top Artists ---
        artists = sp.current_user_top_artists(limit=20, time_range=tr).get("items", [])
        if artists:
            artist_df = pd.DataFrame(
                [{
                    "Rank": i + 1,
                    "Artist": a.get("name", "Unknown Artist"),
                    "ID": a.get("id", "")
                } for i, a in enumerate(artists)]
            )
            artist_out = os.path.join(range_dir, "artists.csv")
            artist_df.to_csv(artist_out, index=False)
            print(f"✅ Saved {len(artist_df)} artists to {artist_out}")
        else:
            print(f"⚠️ No top artists found for {tr}")

        # --- Top Tracks ---
        tracks = get_top_tracks(sp, tr, total_limit=100) or []
        if tracks:
            track_df = pd.DataFrame(
                [{
                    "Rank": i + 1,
                    "Track": t.get("name", "Unknown Track"),
                    "Artist": (t.get("artists") or [{}])[0].get("name", "Unknown Artist"),
                    "ID": t.get("id", "")
                } for i, t in enumerate(tracks)]
            )
            track_out = os.path.join(range_dir, "tracks.csv")
            track_df.to_csv(track_out, index=False)
            print(f"✅ Saved {len(track_df)} tracks to {track_out}")
        else:
            print(f"⚠️ No top tracks found for {tr}")

        all_data[tr] = {"artists": artists, "tracks": tracks}

    # Which term to display
    time_range = request.args.get("time_range", "medium_term")

    return render_template(
        "summary.html",
        display_name=custom_name,
        artists=all_data[time_range]["artists"][:10],
        tracks=all_data[time_range]["tracks"][:10],
        time_range=time_range,
    )

"""
Stripify is a music data analysis project. It takes a Spotify data export as input, builds a
local SQLite database from it, runs a set of creative SQL queries to surface listening habits,
and feeds those insights into an LLM to recommend fresh tracks tailored to the user's taste.

Input files:
    StreamingHistory_music_0.json - Full playback history
    Wrapped2024.json              - Official Spotify Wrapped data

These files can be downloaded from https://www.spotify.com/us/account/privacy/. The standard
(not extended, not technical) data package includes everything this project needs, and may take
Spotify a few days to prepare.

How it works:
    1. Database creation      - The streaming history is loaded into a local SQLite database.
    2. Feature extraction      - Several SQL queries explore listening habits; results are
                                 compiled into mega_wrapped.csv.
    3. Prompted recommendations - The official Wrapped data (not the full history, to avoid
                                 recommending already-known tracks) is used to prompt an LLM for
                                 five new tracks.
    4. Checker-corrector       - A second LLM pass re-checks those recommendations against the
                                 known artists/tracks, saving the result to fresh_tracks.csv.

Output files:
    mega_wrapped.csv - A detailed breakdown of listening behavior
    fresh_tracks.csv - Personalized new-music suggestions, checked against known tracks/artists

These outputs are ready to be plugged into any visualization layer or dashboard (not included
in this project).
"""

import os
import json
import re
import sqlite3
from io import StringIO
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI

# ----------------------------- Configuration -----------------------------

BASE_DIR = Path(__file__).resolve().parent

HISTORY_JSON = BASE_DIR / "StreamingHistory_music_0.json"
WRAPPED_JSON = BASE_DIR / "Wrapped2024.json"

DB_PATH = BASE_DIR / "spotify_history.db"
MEGA_WRAPPED_CSV = BASE_DIR / "mega_wrapped.csv"
RECOMMENDATIONS_CSV = BASE_DIR / "fresh_tracks.csv"
RAW_RECOMMENDATIONS_CSV = BASE_DIR / "fresh_tracks_raw.csv"

GPT_MODEL = "gpt-4o"
GPT_TEMPERATURE = 0.1

# Mega-wrap SQL queries: title -> (description, SQL)
QUERIES = {
    "Most Repeated Songs": (
        "Tracks played on at least 3 different days.",
        """SELECT t.track_name, t.artist_name FROM Plays p
           JOIN Tracks t ON p.track_id = t.track_id
           GROUP BY p.track_id
           HAVING COUNT(DISTINCT SUBSTR(p.played_at,1,10))>=3
           ORDER BY COUNT(*) DESC LIMIT 5;"""
    ),
    "Top Artists by Completion": (
        "Artists ranked by your average listening duration.",
        """SELECT artist_name FROM (
               SELECT t.artist_name, AVG(p.ms_played) avg_play FROM Plays p
               JOIN Tracks t ON p.track_id=t.track_id GROUP BY t.track_id
               HAVING COUNT(*)>1) GROUP BY artist_name
           ORDER BY AVG(avg_play) DESC LIMIT 5;"""
    ),
    "Skipped Songs": (
        "Tracks frequently skipped despite multiple plays.",
        """SELECT t.track_name, t.artist_name FROM Plays p
           JOIN Tracks t ON p.track_id=t.track_id GROUP BY p.track_id
           HAVING SUM(p.ms_played<30000)>=2 AND COUNT(*)>=3
           ORDER BY COUNT(*) DESC LIMIT 5;"""
    ),
    "Sleeper Hits": (
        "Tracks initially skipped but later enjoyed significantly more.",
        """WITH fl AS (SELECT track_id, MIN(played_at) f, MAX(played_at) l FROM Plays GROUP BY track_id),
              segments AS (SELECT p.track_id, CASE
                  WHEN played_at <= DATE(f,'+7 days') THEN 'early'
                  WHEN played_at >= DATE(l,'-7 days') THEN 'late' END period, ms_played FROM Plays p JOIN fl ON p.track_id=fl.track_id),
              changes AS (SELECT track_id FROM segments GROUP BY track_id HAVING AVG(CASE WHEN period='late' THEN ms_played END)>AVG(CASE WHEN period='early' THEN ms_played END)*2)
           SELECT t.track_name, t.artist_name FROM Tracks t JOIN changes ON t.track_id=changes.track_id LIMIT 5;"""
    ),
    "One-Week Obsessions": (
        "Tracks intensively listened to for exactly one week.",
        """WITH weeks AS (SELECT track_id, STRFTIME('%Y-%W',played_at) wk FROM Plays GROUP BY track_id,wk),
              one_wk AS (SELECT track_id FROM weeks GROUP BY track_id HAVING COUNT(wk)=1)
           SELECT t.track_name, t.artist_name FROM Tracks t JOIN one_wk ON t.track_id=one_wk.track_id LIMIT 5;"""
    ),
    "Immersive Tracks": (
        "Tracks frequently played in full (rarely skipped).",
        """SELECT t.track_name, t.artist_name FROM Plays p JOIN Tracks t ON p.track_id=t.track_id
           GROUP BY p.track_id HAVING AVG(ms_played)>300000 AND COUNT(*)>=3 ORDER BY AVG(ms_played) DESC LIMIT 5;"""
    ),
}


def _extract_csv(text: str) -> str:
    """Strip an optional ```/```csv code fence and trailing-space line breaks GPT sometimes adds."""
    text = text.strip()
    text = re.sub(r"^```[\w-]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def _parse_recommendations(raw_response: str) -> pd.DataFrame:
    """Parse a GPT response into a title/artist/comment DataFrame, or fail with a clear message."""
    csv_text = _extract_csv(raw_response)
    try:
        df = pd.read_csv(StringIO(csv_text))
    except Exception as exc:
        raise RuntimeError(
            f"Could not parse GPT's response as CSV ({exc}).\nRaw response:\n{raw_response}"
        ) from exc

    missing = {"title", "artist", "comment"} - set(df.columns)
    if missing:
        raise RuntimeError(
            f"GPT's response is missing expected column(s) {sorted(missing)}.\nRaw response:\n{raw_response}"
        )
    return df


def get_openai_client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Export it in your shell "
            "(e.g. `export OPENAI_API_KEY=sk-...`) or put it in a .env file before running."
        )
    return OpenAI(api_key=api_key)


def create_spotify_db(history_file: Path, db_path: Path) -> None:
    """Load a Spotify streaming history export into a fresh local SQLite database."""
    if db_path.exists():
        db_path.unlink()

    with open(history_file, "r", encoding="utf-8") as f:
        history = json.load(f)

    df = pd.DataFrame(history)
    df["trackName"] = df["trackName"].str.strip()
    df["artistName"] = df["artistName"].str.strip()

    conn = sqlite3.connect(db_path)
    try:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS Tracks (
                track_id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_name TEXT,
                artist_name TEXT,
                UNIQUE(track_name, artist_name)
            );
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS Plays (
                play_id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id INTEGER,
                played_at TEXT,
                ms_played INTEGER,
                FOREIGN KEY(track_id) REFERENCES Tracks(track_id)
            );
        """)

        tracks = df[["trackName", "artistName"]].drop_duplicates().values.tolist()
        c.executemany(
            "INSERT OR IGNORE INTO Tracks (track_name, artist_name) VALUES (?, ?)", tracks
        )

        track_ids = dict(c.execute("SELECT track_name || '\x1f' || artist_name, track_id FROM Tracks").fetchall())
        plays = [
            (track_ids[f"{row.trackName}\x1f{row.artistName}"], row.endTime, row.msPlayed)
            for row in df.itertuples()
        ]
        c.executemany(
            "INSERT INTO Plays (track_id, played_at, ms_played) VALUES (?, ?, ?)", plays
        )

        conn.commit()
    finally:
        conn.close()


def run_queries(db_path: Path, queries: dict) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        return {title: (desc, pd.read_sql(sql, conn)) for title, (desc, sql) in queries.items()}
    finally:
        conn.close()


def get_gpt_recommendations(client: OpenAI, wrapped_data: dict) -> pd.DataFrame:
    """Ask GPT for 5 fresh tracks, steering clear of the user's known artists/tracks."""
    known_artists = [a["artistUri"] for a in wrapped_data["topArtists"]["topArtists"]]
    known_tracks = wrapped_data["topTracks"]["topTracks"]

    prompt = f"""
    Known artists: {', '.join(known_artists)}.
    Known tracks: {', '.join(known_tracks)}.
    Recommend 5 completely new songs avoiding known artists/tracks.
    Output as plain CSV rows (no surrounding quotes, no code fence) with header: title,artist,comment
    """

    response = client.chat.completions.create(
        model=GPT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=GPT_TEMPERATURE,
    )

    return _parse_recommendations(response.choices[0].message.content)


def correct_recommendations(client: OpenAI, recommendations_df: pd.DataFrame, wrapped_data: dict) -> pd.DataFrame:
    """Second pass: ask GPT to double-check the recommendations against known artists/tracks."""
    known_artists = ", ".join(a["artistUri"] for a in wrapped_data["topArtists"]["topArtists"])
    known_tracks = ", ".join(wrapped_data["topTracks"]["topTracks"])

    prompt = f"""
    Known artists: {known_artists}.
    Known tracks: {known_tracks}.

    Below is a CSV of recommended tracks (title,artist,comment). Remove any row whose track or
    artist is already in the known lists above. Return only the corrected CSV, header included,
    with no surrounding quotes, no code fence, and no extra commentary.

    {recommendations_df.to_csv(index=False)}
    """

    response = client.chat.completions.create(
        model=GPT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=GPT_TEMPERATURE,
    )

    return _parse_recommendations(response.choices[0].message.content)


def main():
    load_dotenv(BASE_DIR / ".env")
    print("Stripify: Spotify Wrapped Enhanced\n")

    if not HISTORY_JSON.exists():
        raise FileNotFoundError(
            f"Missing {HISTORY_JSON.name}. Download your Spotify data export and place it next "
            "to this script (see README for instructions)."
        )
    if not WRAPPED_JSON.exists():
        raise FileNotFoundError(
            f"Missing {WRAPPED_JSON.name}. Download your Spotify Wrapped export and place it "
            "next to this script (see README for instructions)."
        )

    create_spotify_db(HISTORY_JSON, DB_PATH)

    mega_results = run_queries(DB_PATH, QUERIES)

    mega_df = pd.concat(
        [df.assign(category=title) for title, (_, df) in mega_results.items()],
        ignore_index=True,
    )
    mega_df.to_csv(MEGA_WRAPPED_CSV, index=False)

    for title, (desc, df) in mega_results.items():
        tracks_artists = [f"{row[0]} by {row[1]}" if len(row) > 1 else row[0] for row in df.values]
        print(f"{title}\n{desc}\nTop picks: {', '.join(tracks_artists)}\n")

    with open(WRAPPED_JSON, encoding="utf-8") as f:
        wrapped_data = json.load(f)

    client = get_openai_client()

    raw_recs_df = get_gpt_recommendations(client, wrapped_data)
    raw_recs_df.to_csv(RAW_RECOMMENDATIONS_CSV, index=False)

    final_df = correct_recommendations(client, raw_recs_df, wrapped_data)
    final_df.to_csv(RECOMMENDATIONS_CSV, index=False)
    recs = [f"{row['title']} by {row['artist']}" for _, row in final_df.iterrows()]
    print("Fresh Tracks Recommendations\nNew songs tailored for you.\nTop picks:", ', '.join(recs))
    print(f"\nSaved to {RECOMMENDATIONS_CSV.name} (raw pass kept in {RAW_RECOMMENDATIONS_CSV.name} for reference).")


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"\nStripify stopped: {exc}")

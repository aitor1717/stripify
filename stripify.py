import os
import json
import sqlite3
import pandas as pd
from pathlib import Path
from openai import OpenAI

# =====================================================================================
# Stripify
# =====================================================================================
# Generates a personalized mega-wrap and fresh track recommendations using GPT
# Inputs: Spotify streaming history and wrapped data
# Outputs: mega_wrapped.csv, fresh_tracks.csv
# =====================================================================================

# Configuration

api_key = "OPENAI_API_KEY"

BASE_DIR = Path(__file__).resolve().parent
HISTORY_JSON = BASE_DIR / "StreamingHistory_music_0.json"
WRAPPED_JSON = BASE_DIR / "Wrapped2024.json"
DB_NAME = BASE_DIR / "spotify_history.db"
MEGA_WRAPPED_CSV = BASE_DIR / "mega_wrapped.csv"
RECOMMENDATIONS_CSV = BASE_DIR / "fresh_tracks.csv"

GPT_MODEL = "gpt-4o"
GPT_TEMPERATURE = 0.1

openai_client = OpenAI(api_key=api_key)

# Database Creation

def create_spotify_db(history_file, db_path):

    with open(history_file, "r", encoding="utf-8") as f:
        history = json.load(f)

    df = pd.DataFrame(history)
    df["trackName"] = df["trackName"].str.strip()
    df["artistName"] = df["artistName"].str.strip()

    conn = sqlite3.connect(db_path)
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

    for _, row in df.iterrows():

        c.execute("INSERT OR IGNORE INTO Tracks (track_name, artist_name) VALUES (?, ?)",
                  (row["trackName"], row["artistName"]))
        tid = c.execute("SELECT track_id FROM Tracks WHERE track_name = ? AND artist_name = ?",
                        (row["trackName"], row["artistName"])).fetchone()[0]
        c.execute("INSERT INTO Plays (track_id, played_at, ms_played) VALUES (?, ?, ?)",
                  (tid, row["endTime"], row["msPlayed"]))
        
    conn.commit()
    conn.close()

# Mega-Wrap Queries

queries = {

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
    )
}

def run_queries(db, queries):

    conn = sqlite3.connect(db)
    results = {}
    for title, (desc, sql) in queries.items():
        results[title] = (desc, pd.read_sql(sql, conn))
    conn.close()

    return results

# Get Recommendations

def get_gpt_recommendations(wrapped_data):

    known_artists = [a["artistUri"] for a in wrapped_data["topArtists"]["topArtists"]]
    known_tracks = wrapped_data["topTracks"]["topTracks"]

    prompt = f"""
    Known artists: {', '.join(known_artists)}.
    Known tracks: {', '.join(known_tracks)}.
    Recommend 5 completely new songs avoiding known artists/tracks.
    Output in CSV: title,artist,comment.
    """

    response = openai_client.chat.completions.create(
        model=GPT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=GPT_TEMPERATURE
    )

    lines = response.choices[0].message.content.strip().split('\n')
    data = [line.split(",", 2) for line in lines if line.count(",") == 2]

    return pd.DataFrame(data[1:], columns=data[0])

# Main

def main():

    print("Stripify: Spotify Wrapped Enhanced\n")

    create_spotify_db(HISTORY_JSON, DB_NAME)

    mega_results = run_queries(DB_NAME, queries)

    mega_df = pd.concat([
        df.assign(category=title) for title, (_, df) in mega_results.items()
    ], ignore_index=True)

    mega_df.to_csv(MEGA_WRAPPED_CSV, index=False)

    for title, (desc, df) in mega_results.items():
        tracks_artists = [f"{row[0]} by {row[1]}" if len(row) > 1 else row[0] for row in df.values]
        print(f"{title}\n{desc}\nTop picks: {', '.join(tracks_artists)}\n")

    with open(WRAPPED_JSON, encoding="utf-8") as f:
        wrapped_data = json.load(f)

    fresh_tracks_df = get_gpt_recommendations(wrapped_data)
    fresh_tracks_df.to_csv(RECOMMENDATIONS_CSV, index=False)

    recs = [f"{row['title']} by {row['artist']}" for _, row in fresh_tracks_df.iterrows()]
    print("Fresh Tracks Recommendations\nNew songs tailored for you.\nTop picks:", ', '.join(recs))

if __name__ == "__main__":
    main()

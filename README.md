<p align="center">
  <img src="logo_l.png" alt="Stripify logo" width="480">
</p>

# Stripify

Stripify turns a Spotify data export into a mega-wrap and a set of fresh music recommendations from an LLM. It extracts structured insights from listening history and official Wrapped metrics, builds a local database, runs a handful of purpose-built SQL queries, and calls an LLM to surface hidden-gem tracks.

## How it works

1. **Database creation**: Streaming history is loaded into a local SQLite database.
2. **Feature extraction**: Six creative SQL queries explore listening habits; the results are compiled into `mega_wrapped.csv`.
3. **Prompted recommendations**: Using only the official Wrapped data to avoid recommending repeated tracks, an LLM is prompted for new tracks.
4. **Checker-corrector**: A second GPT call re-checks the recommendations against your artists and tracks, saving the result to `fresh_tracks.csv`.

### Queries

| Query | What it finds |
| --- | --- |
| Most Repeated Songs | Tracks played on at least 3 separate days, true recurring favorites |
| Top Artists by Completion | Artists whose songs are consistently listened to the longest |
| Skipped Songs | Songs played 3+ times but skipped (under 30s) more than once |
| Sleeper Hits | Songs initially skipped but now listened to much longer |
| One-Week Obsessions | Songs binged exclusively during one calendar week, then never played again |
| Immersive Tracks | Tracks rarely skipped and often played for 5+ minutes |

## Setup

```bash
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...   # or put it in a .env file
```

### Input files

Place these next to `stripify.py`:

- `StreamingHistory_music_0.json`: Full playback history
- `Wrapped2024.json`: Official Spotify Wrapped data

Both are available from [Spotify Privacy Portal](https://www.spotify.com/us/account/privacy/). Request the **standard** package (not extended or technical). It may take Spotify a few days to prepare.

## Running it

```bash
python stripify.py
```

This produces:

- `mega_wrapped.csv`: A detailed breakdown of listening behavior
- `fresh_tracks.csv`: Personalized new music suggestions, checked against known tracks and artists

These are plain CSVs, ready to be plugged into any visualization layer or dashboard (not included in this project).

## License

MIT

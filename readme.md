Stripify

Stripify is a music data analysis project that transforms a user's Spotify data into a sophisticated "mega-wrap" and fresh music recommendations using GPT. It extracts structured insights from listening history and wrapped metrics, builds a local database, runs custom SQL queries, and generates recommendations designed to surface hidden gems.

~ Input Files

    OpenAI API key

    StreamingHistory_music_0.json – Your full playback history

    Wrapped2024.json – Official Spotify Wrapped data

These files can be downloaded directly from your Spotify Privacy Portal.
It may take a few days for Spotify to process and generate the standard data package. You do not need the extended or technical version.

~ How It Works

    Database Creation
    The user's streaming history is converted into a local SQLite database.

    Feature Extraction via SQL
    Several creative queries are run to explore your listening habits. The results are compiled into a single mega_wrapped.csv file.

    Prompted Recommendations
    Using only the official Wrapped data to avoid overlap, a GPT model is prompted to recommend new tracks.
    Results are saved to fresh_tracks.csv.

~ SQL Queries Explained

    Most Repeated Songs Across Different Days
    Tracks played on at least three separate days — your true recurring favorites.

    Top Artists by Average Song Completion
    Artists whose songs you consistently listened to the longest on average.

    Skipped Songs You Tried to Like
    Songs you played at least three times but skipped (under 30 seconds) more than once.

    Sleeper Hits
    Songs you initially skipped but eventually listened to significantly more.

    One-Week Obsessions
    Songs binged exclusively during one calendar week, then never played again.

    Most Immersive Songs
    Tracks you rarely skipped and often played for over 5 minutes on average.

~ GPT Recommendations

Based on the insights extracted, a prompt is constructed and sent to GPT to recommend new songs—excluding any previously known tracks or artists. The output is saved in fresh_tracks.csv.

~ Output Files

    mega_wrapped.csv – A detailed breakdown of your listening behavior

    fresh_tracks.csv – Personalized new music suggestions

These outputs are formatted and ready to be plugged into any visualization layer or dashboard if desired (not included in this project).

~ Results:

    Preliminary:
    The sample Sporify dataset used comes from an account that is seldom used. The wrap results are roughly accurate. From the recommended tracks,
    accross two iterations, three were already known and three were quite good. One track was mismatched from their artist.
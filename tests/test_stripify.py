import json
import sqlite3
from types import SimpleNamespace

import pandas as pd
import pytest

import stripify


def play(end_time, artist, track, ms):
    return {"endTime": end_time, "artistName": artist, "trackName": track, "msPlayed": ms}


# Each track is shaped to land in (or stay out of) specific queries.
HISTORY = [
    # Repeat: played on 3 separate days in 3 different weeks -> Most Repeated
    play("2024-01-01 10:00", "Artist R", "Repeat", 200000),
    play("2024-01-10 10:00", "Artist R", "Repeat", 200000),
    play("2024-01-20 10:00", "Artist R", "Repeat", 200000),
    # Binge: 4 plays on one day, never again -> One-Week Obsession, not Most Repeated
    play("2024-02-05 09:00", "Artist B", "Binge", 180000),
    play("2024-02-05 10:00", "Artist B", "Binge", 180000),
    play("2024-02-05 11:00", "Artist B", "Binge", 180000),
    play("2024-02-05 12:00", "Artist B", "Binge", 180000),
    # Skippy: 3 plays, 2 under 30s -> Skipped Songs
    play("2024-01-02 10:00", "Artist K", "Skippy", 10000),
    play("2024-01-15 10:00", "Artist K", "Skippy", 12000),
    play("2024-01-25 10:00", "Artist K", "Skippy", 150000),
    # Sleeper: short early plays, long late plays -> Sleeper Hits
    play("2024-01-03 10:00", "Artist S", "Sleeper", 5000),
    play("2024-01-04 10:00", "Artist S", "Sleeper", 5000),
    play("2024-03-01 10:00", "Artist S", "Sleeper", 240000),
    play("2024-03-02 10:00", "Artist S", "Sleeper", 240000),
    # Epic: 3 plays over 5 minutes -> Immersive Tracks, top artist by completion
    play("2024-01-05 10:00", "Artist E", "Epic", 400000),
    play("2024-01-12 10:00", "Artist E", "Epic", 420000),
    play("2024-01-19 10:00", "Artist E", "Epic", 410000),
]

WRAPPED = {
    "topArtists": {"topArtists": [{"artistUri": "spotify:artist:known1"}, {"artistUri": "spotify:artist:known2"}]},
    "topTracks": {"topTracks": ["spotify:track:knownA", "spotify:track:knownB"]},
}


@pytest.fixture
def history_file(tmp_path):
    path = tmp_path / "history.json"
    path.write_text(json.dumps(HISTORY), encoding="utf-8")
    return path


@pytest.fixture
def db_path(tmp_path, history_file):
    path = tmp_path / "history.db"
    stripify.create_spotify_db(history_file, path)
    return path


@pytest.fixture
def results(db_path):
    return {title: df for title, (_, df) in stripify.run_queries(db_path, stripify.QUERIES).items()}


def track_names(df):
    return set(df["track_name"])


class FakeClient:
    """Stands in for openai.OpenAI: returns canned replies in order and records the prompts."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, model, messages, temperature):
        self.prompts.append(messages[0]["content"])
        content = self.replies.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


# ----------------------------- Response parsing -----------------------------

class TestExtractCsv:
    def test_plain_text_is_unchanged(self):
        assert stripify._extract_csv("title,artist\na,b") == "title,artist\na,b"

    def test_strips_csv_code_fence(self):
        assert stripify._extract_csv("```csv\ntitle,artist\na,b\n```") == "title,artist\na,b"

    def test_strips_bare_code_fence(self):
        assert stripify._extract_csv("```\ntitle,artist\na,b\n```") == "title,artist\na,b"

    def test_strips_trailing_spaces_and_outer_whitespace(self):
        assert stripify._extract_csv("\n  title,artist  \na,b   \n\n") == "title,artist\na,b"


class TestParseRecommendations:
    def test_parses_fenced_csv(self):
        df = stripify._parse_recommendations("```csv\ntitle,artist,comment\nSong,Band,Nice\n```")
        assert df.to_dict("records") == [{"title": "Song", "artist": "Band", "comment": "Nice"}]

    def test_missing_column_raises_with_raw_response(self):
        with pytest.raises(RuntimeError, match=r"missing expected column\(s\) \['comment'\]") as exc:
            stripify._parse_recommendations("title,artist\nSong,Band")
        assert "Song,Band" in str(exc.value)

    def test_non_csv_response_raises(self):
        with pytest.raises(RuntimeError):
            stripify._parse_recommendations("Sorry, I can't help with that.")


# ----------------------------- Database -----------------------------

class TestCreateSpotifyDb:
    def test_loads_tracks_and_plays(self, db_path):
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM Tracks").fetchone()[0] == 5
            assert conn.execute("SELECT COUNT(*) FROM Plays").fetchone()[0] == len(HISTORY)

    def test_whitespace_variants_map_to_one_track(self, tmp_path):
        history = tmp_path / "h.json"
        history.write_text(json.dumps([
            play("2024-01-01 10:00", "Band", "Song", 1000),
            play("2024-01-02 10:00", " Band ", "Song  ", 2000),
        ]), encoding="utf-8")
        db = tmp_path / "h.db"
        stripify.create_spotify_db(history, db)
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT track_name, artist_name FROM Tracks").fetchall() == [("Song", "Band")]
            assert conn.execute("SELECT COUNT(DISTINCT track_id) FROM Plays").fetchone()[0] == 1

    def test_same_title_different_artists_are_separate_tracks(self, tmp_path):
        history = tmp_path / "h.json"
        history.write_text(json.dumps([
            play("2024-01-01 10:00", "Band A", "Intro", 1000),
            play("2024-01-01 11:00", "Band B", "Intro", 1000),
        ]), encoding="utf-8")
        db = tmp_path / "h.db"
        stripify.create_spotify_db(history, db)
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT COUNT(*) FROM Tracks").fetchone()[0] == 2

    def test_rerun_rebuilds_instead_of_appending(self, history_file, db_path):
        stripify.create_spotify_db(history_file, db_path)
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM Plays").fetchone()[0] == len(HISTORY)


# ----------------------------- Queries -----------------------------

class TestQueries:
    def test_every_query_runs(self, results):
        assert set(results) == set(stripify.QUERIES)

    def test_most_repeated_needs_three_distinct_days(self, results):
        names = track_names(results["Most Repeated Songs"])
        assert "Repeat" in names
        assert "Binge" not in names  # 4 plays, but all on one day

    def test_top_artists_by_completion_ranks_longest_listens_first(self, results):
        df = results["Top Artists by Completion"]
        assert list(df.columns) == ["artist_name"]
        assert df["artist_name"].iloc[0] == "Artist E"

    def test_skipped_songs(self, results):
        names = track_names(results["Skipped Songs"])
        assert "Skippy" in names
        assert "Repeat" not in names  # never skipped
        assert "Binge" not in names

    def test_sleeper_hits(self, results):
        names = track_names(results["Sleeper Hits"])
        assert "Sleeper" in names
        assert "Epic" not in names

    def test_one_week_obsessions(self, results):
        names = track_names(results["One-Week Obsessions"])
        assert "Binge" in names
        assert "Repeat" not in names

    def test_immersive_tracks(self, results):
        assert track_names(results["Immersive Tracks"]) == {"Epic"}


# ----------------------------- LLM calls -----------------------------

class TestRecommendations:
    def test_prompt_lists_known_artists_and_tracks(self):
        client = FakeClient("title,artist,comment\nNew,Someone,Fresh")
        df = stripify.get_gpt_recommendations(client, WRAPPED)
        assert df["title"].tolist() == ["New"]
        prompt = client.prompts[0]
        for known in ("spotify:artist:known1", "spotify:artist:known2", "spotify:track:knownA", "spotify:track:knownB"):
            assert known in prompt

    def test_corrector_receives_first_pass_csv(self):
        first = pd.DataFrame([{"title": "New", "artist": "Someone", "comment": "Fresh"}])
        client = FakeClient("```csv\ntitle,artist,comment\n```")
        df = stripify.correct_recommendations(client, first, WRAPPED)
        assert "New,Someone,Fresh" in client.prompts[0]
        assert df.empty
        assert list(df.columns) == ["title", "artist", "comment"]


class TestOpenAiClient:
    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY is not set"):
            stripify.get_openai_client()

    def test_key_from_environment(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert stripify.get_openai_client().api_key == "sk-test"


# ----------------------------- End to end -----------------------------

@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Point every stripify path at tmp_path so main() never touches the real project folder."""
    monkeypatch.setattr(stripify, "BASE_DIR", tmp_path)
    for name, filename in [
        ("HISTORY_JSON", "StreamingHistory_music_0.json"),
        ("WRAPPED_JSON", "Wrapped2024.json"),
        ("DB_PATH", "spotify_history.db"),
        ("MEGA_WRAPPED_CSV", "mega_wrapped.csv"),
        ("RECOMMENDATIONS_CSV", "fresh_tracks.csv"),
        ("RAW_RECOMMENDATIONS_CSV", "fresh_tracks_raw.csv"),
    ]:
        monkeypatch.setattr(stripify, name, tmp_path / filename)
    return tmp_path


class TestMain:
    def test_full_pipeline_writes_outputs(self, workspace, monkeypatch):
        (workspace / "StreamingHistory_music_0.json").write_text(json.dumps(HISTORY), encoding="utf-8")
        (workspace / "Wrapped2024.json").write_text(json.dumps(WRAPPED), encoding="utf-8")
        client = FakeClient(
            "title,artist,comment\nNew,Someone,Fresh\nOld,Artist R,Known",
            "title,artist,comment\nNew,Someone,Fresh",
        )
        monkeypatch.setattr(stripify, "get_openai_client", lambda: client)

        stripify.main()

        mega = pd.read_csv(workspace / "mega_wrapped.csv")
        assert set(mega["category"]) <= set(stripify.QUERIES)
        assert "Repeat" in set(mega["track_name"])
        assert len(pd.read_csv(workspace / "fresh_tracks_raw.csv")) == 2
        assert pd.read_csv(workspace / "fresh_tracks.csv")["title"].tolist() == ["New"]

    def test_api_key_loaded_from_dotenv(self, workspace, monkeypatch):
        (workspace / "StreamingHistory_music_0.json").write_text(json.dumps(HISTORY), encoding="utf-8")
        (workspace / "Wrapped2024.json").write_text(json.dumps(WRAPPED), encoding="utf-8")
        (workspace / ".env").write_text("OPENAI_API_KEY=sk-from-dotenv\n", encoding="utf-8")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        keys = []

        def fake_openai(api_key):
            keys.append(api_key)
            return FakeClient("title,artist,comment\nNew,Someone,Fresh", "title,artist,comment\nNew,Someone,Fresh")

        monkeypatch.setattr(stripify, "OpenAI", fake_openai)
        stripify.main()
        assert keys == ["sk-from-dotenv"]

    def test_missing_history_file(self, workspace):
        with pytest.raises(FileNotFoundError, match="StreamingHistory_music_0.json"):
            stripify.main()

    def test_missing_wrapped_file(self, workspace):
        (workspace / "StreamingHistory_music_0.json").write_text(json.dumps(HISTORY), encoding="utf-8")
        with pytest.raises(FileNotFoundError, match="Wrapped2024.json"):
            stripify.main()

"""SEC Stadium Tap - backend (v2: 16-stadium playthrough)
Each player can attempt each of the 16 stadiums once.  Within 5km of the real
location unlocks 3 trivia questions that boost the score.  Total of all 16
attempts is the player's leaderboard score.
"""

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
from typing import Optional, List
from datetime import datetime
import sqlite3
import json
import os
import uuid
import math

APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "data"
STATIC_DIR = APP_DIR.parent / "static"
DB_PATH = os.environ.get("DB_PATH", "/app/data/game.db")
INVITE_CODE = os.environ.get("INVITE_CODE", "secstadiums")

with open(DATA_DIR / "stadiums.json") as f:
    STADIUMS = json.load(f)
with open(DATA_DIR / "trivia.json") as f:
    TRIVIA = json.load(f)
STADIUM_KEYS = sorted(STADIUMS.keys(), key=lambda k: STADIUMS[k]["team"])

app = FastAPI(title="SEC Stadium Tap")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS players (
            token TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_token TEXT NOT NULL,
            stadium_key TEXT NOT NULL,
            guess_lat REAL NOT NULL,
            guess_lng REAL NOT NULL,
            distance_km REAL NOT NULL,
            base_score INTEGER NOT NULL,
            trivia_correct INTEGER NOT NULL DEFAULT 0,
            trivia_submitted INTEGER NOT NULL DEFAULT 0,
            multiplier REAL NOT NULL DEFAULT 1.0,
            final_score INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(player_token, stadium_key)
        );
        """
    )
    conn.commit()
    conn.close()


init_db()


def haversine(lat1, lng1, lat2, lng2):
    R = 6371.0
    lat1r, lng1r, lat2r, lng2r = map(math.radians, [lat1, lng1, lat2, lng2])
    dlat = lat2r - lat1r
    dlng = lng2r - lng1r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1r) * math.cos(lat2r) * math.sin(dlng / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def calc_base_score(distance_km: float) -> int:
    return max(0, int(round(1000 * (1 - distance_km / 2000))))


def get_player(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing token")
    token = authorization[7:]
    conn = get_db()
    row = conn.execute("SELECT * FROM players WHERE token = ?", (token,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(401, "Invalid token")
    return dict(row)


class JoinRequest(BaseModel):
    name: str
    invite_code: str


@app.post("/api/join")
def join(req: JoinRequest):
    if req.invite_code.strip() != INVITE_CODE:
        raise HTTPException(403, "Wrong invite code")
    name = req.name.strip()[:30]
    if not name:
        raise HTTPException(400, "Name required")
    token = str(uuid.uuid4())
    conn = get_db()
    conn.execute(
        "INSERT INTO players (token, name, created_at) VALUES (?, ?, ?)",
        (token, name, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
    return {"token": token, "name": name}


@app.get("/api/stadiums")
def stadiums_list(player=Depends(get_player)):
    """Return the full 16-stadium list with the player's attempt state for each."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM attempts WHERE player_token = ?",
        (player["token"],),
    ).fetchall()
    conn.close()
    by_key = {r["stadium_key"]: dict(r) for r in rows}

    out = []
    for i, key in enumerate(STADIUM_KEYS, start=1):
        s = STADIUMS[key]
        entry = {
            "number": i,
            "key": key,
            "team": s["team"],
            "stadium_name": s["name"],
            "city": s["city"],
            "played": key in by_key,
        }
        if key in by_key:
            a = by_key[key]
            entry["result"] = {
                "distance_km": a["distance_km"],
                "stadium_lat": s["lat"],
                "stadium_lng": s["lng"],
                "guess_lat": a["guess_lat"],
                "guess_lng": a["guess_lng"],
                "base_score": a["base_score"],
                "trivia_correct": a["trivia_correct"],
                "trivia_submitted": bool(a["trivia_submitted"]),
                "multiplier": a["multiplier"],
                "final_score": a["final_score"],
                "unlocked": a["distance_km"] <= 10.0,
            }
        out.append(entry)
    return out


class GuessRequest(BaseModel):
    stadium_key: str
    lat: float
    lng: float


@app.post("/api/guess")
def guess(req: GuessRequest, player=Depends(get_player)):
    if req.stadium_key not in STADIUMS:
        raise HTTPException(400, "Unknown stadium")
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM attempts WHERE player_token = ? AND stadium_key = ?",
        (player["token"], req.stadium_key),
    ).fetchone()
    if existing:
        conn.close()
        raise HTTPException(400, "Already attempted this stadium")

    stadium = STADIUMS[req.stadium_key]
    distance = haversine(req.lat, req.lng, stadium["lat"], stadium["lng"])
    base_score = calc_base_score(distance)
    unlocked = distance <= 10.0

    conn.execute(
        """INSERT INTO attempts
        (player_token, stadium_key, guess_lat, guess_lng, distance_km,
         base_score, trivia_correct, trivia_submitted, multiplier, final_score, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 0, 0, 1.0, ?, ?)""",
        (
            player["token"], req.stadium_key, req.lat, req.lng,
            distance, base_score, base_score, datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()
    conn.close()

    resp = {
        "stadium_key": req.stadium_key,
        "distance_km": round(distance, 2),
        "stadium_lat": stadium["lat"],
        "stadium_lng": stadium["lng"],
        "stadium_name": stadium["name"],
        "team": stadium["team"],
        "city": stadium["city"],
        "base_score": base_score,
        "unlocked": unlocked,
    }
    if unlocked:
        resp["trivia"] = [
            {"difficulty": q["difficulty"], "question": q["question"], "options": q["options"]}
            for q in TRIVIA[req.stadium_key]["questions"]
        ]
    return resp


class TriviaRequest(BaseModel):
    stadium_key: str
    answers: List[str]


@app.post("/api/trivia")
def submit_trivia(req: TriviaRequest, player=Depends(get_player)):
    if req.stadium_key not in STADIUMS:
        raise HTTPException(400, "Unknown stadium")
    if len(req.answers) != 3:
        raise HTTPException(400, "Need exactly 3 answers")

    conn = get_db()
    row = conn.execute(
        "SELECT * FROM attempts WHERE player_token = ? AND stadium_key = ?",
        (player["token"], req.stadium_key),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(400, "Must guess first")
    attempt = dict(row)
    if attempt["trivia_submitted"]:
        conn.close()
        raise HTTPException(400, "Trivia already submitted for this stadium")
    if attempt["distance_km"] > 10.0:
        conn.close()
        raise HTTPException(400, "Did not unlock trivia (not within 10km)")

    questions = TRIVIA[req.stadium_key]["questions"]
    correct = sum(1 for q, a in zip(questions, req.answers) if q["answer"] == a)
    multipliers = {0: 1.0, 1: 1.33, 2: 1.66, 3: 2.0}
    mult = multipliers[correct]
    final = int(round(attempt["base_score"] * mult))

    conn.execute(
        """UPDATE attempts SET trivia_correct = ?, trivia_submitted = 1,
           multiplier = ?, final_score = ? WHERE id = ?""",
        (correct, mult, final, attempt["id"]),
    )
    conn.commit()
    conn.close()

    feedback = [
        {
            "question": q["question"],
            "your_answer": a,
            "correct_answer": q["answer"],
            "correct": q["answer"] == a,
        }
        for q, a in zip(questions, req.answers)
    ]
    return {"correct": correct, "multiplier": mult, "final_score": final, "feedback": feedback}


@app.get("/api/leaderboard")
def leaderboard(player=Depends(get_player)):
    conn = get_db()
    rows = conn.execute(
        """
        SELECT p.name,
               COUNT(a.id) AS stadiums_played,
               COALESCE(SUM(a.final_score), 0) AS total_score,
               COALESCE(ROUND(AVG(a.final_score)), 0) AS avg_score,
               COALESCE(MIN(a.distance_km), 0) AS best_distance_km
        FROM players p
        LEFT JOIN attempts a ON a.player_token = p.token
        GROUP BY p.token, p.name
        HAVING stadiums_played > 0
        ORDER BY total_score DESC
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/history")
def history(player=Depends(get_player)):
    conn = get_db()
    rows = conn.execute(
        """SELECT created_at, stadium_key, distance_km, base_score,
                  trivia_correct, trivia_submitted, multiplier, final_score
           FROM attempts WHERE player_token = ? ORDER BY created_at DESC""",
        (player["token"],),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        s = STADIUMS.get(d["stadium_key"], {})
        d["stadium_name"] = s.get("name", d["stadium_key"])
        d["team"] = s.get("team", "")
        out.append(d)
    return out


@app.get("/api/me")
def me(player=Depends(get_player)):
    return {"name": player["name"]}


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))

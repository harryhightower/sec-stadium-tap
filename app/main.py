"""SEC Stadium Tap - backend
Each day all players see the same stadium.  One guess per day.
If within 5km, 3 trivia questions unlock a score multiplier.
"""

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
from typing import Optional, List
from datetime import date, datetime
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
STADIUM_KEYS = sorted(STADIUMS.keys())

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
        CREATE TABLE IF NOT EXISTS plays (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_token TEXT NOT NULL,
            play_date TEXT NOT NULL,
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
            UNIQUE(player_token, play_date)
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


def todays_stadium_key() -> str:
    today = date.today().isoformat()
    h = sum(ord(c) * (i + 1) for i, c in enumerate(today))
    return STADIUM_KEYS[h % len(STADIUM_KEYS)]


def calc_base_score(distance_km: float) -> int:
    # 1000 at 0km, linear to 0 at 2000km, floor 0
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


@app.get("/api/today")
def today(player=Depends(get_player)):
    key = todays_stadium_key()
    stadium = STADIUMS[key]
    today_str = date.today().isoformat()
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM plays WHERE player_token = ? AND play_date = ?",
        (player["token"], today_str),
    ).fetchone()
    conn.close()
    base = {
        "stadium_name": stadium["name"],
        "team": stadium["team"],
        "city": stadium["city"],
        "date": today_str,
    }
    if row:
        p = dict(row)
        base["played"] = True
        base["result"] = {
            "distance_km": p["distance_km"],
            "stadium_lat": stadium["lat"],
            "stadium_lng": stadium["lng"],
            "guess_lat": p["guess_lat"],
            "guess_lng": p["guess_lng"],
            "base_score": p["base_score"],
            "trivia_correct": p["trivia_correct"],
            "trivia_submitted": bool(p["trivia_submitted"]),
            "multiplier": p["multiplier"],
            "final_score": p["final_score"],
            "unlocked": p["distance_km"] <= 5.0,
        }
    else:
        base["played"] = False
    return base


class GuessRequest(BaseModel):
    lat: float
    lng: float


@app.post("/api/guess")
def guess(req: GuessRequest, player=Depends(get_player)):
    today_str = date.today().isoformat()
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM plays WHERE player_token = ? AND play_date = ?",
        (player["token"], today_str),
    ).fetchone()
    if existing:
        conn.close()
        raise HTTPException(400, "Already played today")

    key = todays_stadium_key()
    stadium = STADIUMS[key]
    distance = haversine(req.lat, req.lng, stadium["lat"], stadium["lng"])
    base_score = calc_base_score(distance)
    unlocked = distance <= 5.0

    conn.execute(
        """INSERT INTO plays
        (player_token, play_date, stadium_key, guess_lat, guess_lng, distance_km,
         base_score, trivia_correct, trivia_submitted, multiplier, final_score, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 1.0, ?, ?)""",
        (
            player["token"], today_str, key, req.lat, req.lng,
            distance, base_score, base_score, datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()
    conn.close()

    resp = {
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
            for q in TRIVIA[key]["questions"]
        ]
    return resp


class TriviaRequest(BaseModel):
    answers: List[str]


@app.post("/api/trivia")
def submit_trivia(req: TriviaRequest, player=Depends(get_player)):
    today_str = date.today().isoformat()
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM plays WHERE player_token = ? AND play_date = ?",
        (player["token"], today_str),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(400, "Must guess first")
    play = dict(row)
    if play["trivia_submitted"]:
        conn.close()
        raise HTTPException(400, "Trivia already submitted today")
    if play["distance_km"] > 5.0:
        conn.close()
        raise HTTPException(400, "Did not unlock trivia (not within 5km)")
    if len(req.answers) != 3:
        conn.close()
        raise HTTPException(400, "Need exactly 3 answers")

    key = play["stadium_key"]
    questions = TRIVIA[key]["questions"]
    correct = sum(1 for q, a in zip(questions, req.answers) if q["answer"] == a)
    multipliers = {0: 1.0, 1: 1.33, 2: 1.66, 3: 2.0}
    mult = multipliers[correct]
    final = int(round(play["base_score"] * mult))

    conn.execute(
        """UPDATE plays SET trivia_correct = ?, trivia_submitted = 1,
           multiplier = ?, final_score = ? WHERE id = ?""",
        (correct, mult, final, play["id"]),
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
               COUNT(plays.id) AS days_played,
               COALESCE(SUM(plays.final_score), 0) AS total_score,
               COALESCE(ROUND(AVG(plays.final_score)), 0) AS avg_score,
               COALESCE(MIN(plays.distance_km), 0) AS best_distance_km
        FROM players p
        LEFT JOIN plays ON plays.player_token = p.token
        GROUP BY p.token, p.name
        HAVING days_played > 0
        ORDER BY total_score DESC
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/history")
def history(player=Depends(get_player)):
    conn = get_db()
    rows = conn.execute(
        """SELECT play_date, stadium_key, distance_km, base_score,
                  trivia_correct, trivia_submitted, multiplier, final_score
           FROM plays WHERE player_token = ? ORDER BY play_date DESC""",
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

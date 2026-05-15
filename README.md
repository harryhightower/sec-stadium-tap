# SEC Stadium Tap

Geo-guessing game for SEC football stadiums.  Every day, all players see the same
stadium name.  Drop a pin within 5km of the real location to unlock 3 trivia
questions that boost your score.  Best cumulative total wins.

## Stack

- FastAPI + SQLite (single file db on a persistent Railway volume)
- Leaflet for the map (Carto dark tiles)
- Vanilla JS frontend, mobile first

## Deploy on Railway

1. Create a private repo on GitHub called `sec-stadium-tap`.
2. From this folder:

```
git init
git add .
git commit -m "initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/sec-stadium-tap.git
git push -u origin main
```

3. railway.app -> New Project -> Deploy from GitHub Repo -> pick `sec-stadium-tap`.
4. After first build, click the service -> Settings -> Mounts -> Add Volume,
   mount path `/app/data` (the `railway.toml` declares it but Railway often
   wants a manual confirm).
5. Variables tab -> add `INVITE_CODE` with whatever code you want to share.
6. Settings -> Networking -> Generate Domain.
7. Open the URL, join with your name + invite code, play.

## Local dev

```
pip install -r requirements.txt
DB_PATH=./game.db INVITE_CODE=secstadiums uvicorn app.main:app --reload
```

## Game rules

- One guess per player per day.
- Same stadium for all players each day (date-seeded rotation through 16 SEC teams).
- Distance score: 1000 at 0km, linear down to 0 at 2000km.
- Within 5km -> 3 trivia questions unlock.
- Multipliers: 0 right = 1.0x, 1 = 1.33x, 2 = 1.66x, 3 = 2.0x.
- Final score = distance score * multiplier.

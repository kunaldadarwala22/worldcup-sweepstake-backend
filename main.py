import os
import time
import asyncio
import logging
from typing import Optional

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worldcup-sweepstake")

FOOTBALL_DATA_TOKEN = os.environ.get("FOOTBALL_DATA_TOKEN", "")
BASE_URL = "https://api.football-data.org/v4/competitions/WC"
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "120"))  # 2 min default

app = FastAPI(title="World Cup Sweepstake API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your domain after deploy if you like
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Sweepstake configuration — team name MUST match football-data.org's team
# "name" field as closely as possible. We normalise both sides before
# matching so small differences (United States vs USA) don't break things.
# ---------------------------------------------------------------------------
POOL_PER_PERSON = 25
PLAYERS = {
    "Liam": [
        "Argentina", "Portugal", "Belgium", "Japan", "Colombia", "Sweden",
        "Egypt", "Canada", "Iraq", "Jordan", "Haiti", "South Korea",
    ],
    "Sangita": [
        "Brazil", "Germany", "Croatia", "Morocco", "Uruguay", "Austria",
        "Australia", "Bosnia & Herzegovina", "Saudi Arabia", "Panama",
        "Tunisia", "Ghana",
    ],
    "Nidhi": [
        "France", "Spain", "Switzerland", "Senegal", "Ecuador", "Paraguay",
        "Algeria", "DR Congo", "Uzbekistan", "Cape Verde", "Curacao", "Iran",
    ],
    "Kunal": [
        "England", "Netherlands", "United States", "Mexico", "Ivory Coast",
        "Norway", "Turkey", "South Africa", "Qatar", "New Zealand",
        "Czechia", "Scotland",
    ],
}

# Aliases: our team name -> list of possible names football-data.org might use
ALIASES = {
    "South Korea": ["South Korea", "Korea Republic"],
    "United States": ["United States", "USA"],
    "Ivory Coast": ["Ivory Coast", "Côte d'Ivoire", "Cote d'Ivoire"],
    "Czechia": ["Czechia", "Czech Republic"],
    "Bosnia & Herzegovina": ["Bosnia & Herzegovina", "Bosnia and Herzegovina"],
    "DR Congo": ["DR Congo", "Congo DR", "DR Kongo"],
    "Cape Verde": ["Cape Verde", "Cabo Verde"],
    "Curacao": ["Curacao", "Curaçao"],
}

STAGE_ORDER = [
    "GROUP_STAGE", "LAST_32", "LAST_16", "QUARTER_FINALS",
    "SEMI_FINALS", "THIRD_PLACE", "FINAL",
]
STAGE_LABELS = {
    "GROUP_STAGE": "Group Stage",
    "LAST_32": "Round of 32",
    "LAST_16": "Round of 16",
    "QUARTER_FINALS": "Quarter-Final",
    "SEMI_FINALS": "Semi-Final",
    "THIRD_PLACE": "3rd Place Playoff",
    "FINAL": "Final",
}


def normalise(name: Optional[str]) -> str:
    if not name:
        return ""
    return name.lower().replace("'", "").replace("’", "").replace("-", " ").replace(".", "").strip()


def build_alias_lookup():
    """Map every normalised alias -> our canonical team name."""
    lookup = {}
    for canonical, names in PLAYERS.items():
        pass
    for canonical_list in PLAYERS.values():
        for team in canonical_list:
            lookup[normalise(team)] = team
    for canonical, aliases in ALIASES.items():
        for a in aliases:
            lookup[normalise(a)] = canonical
    return lookup


ALIAS_LOOKUP = build_alias_lookup()

TEAM_TO_PLAYER = {}
for player, teams in PLAYERS.items():
    for t in teams:
        TEAM_TO_PLAYER[t] = player

_cache = {"data": None, "ts": 0}
_cache_lock = asyncio.Lock()


async def fetch_json(client: httpx.AsyncClient, path: str, params: Optional[dict] = None):
    headers = {"X-Auth-Token": FOOTBALL_DATA_TOKEN} if FOOTBALL_DATA_TOKEN else {}
    resp = await client.get(f"{BASE_URL}{path}", headers=headers, params=params or {}, timeout=20)
    if resp.status_code == 429:
        logger.warning("Rate limited by football-data.org")
        return None
    resp.raise_for_status()
    return resp.json()


def resolve_team(name: Optional[str]) -> Optional[str]:
    return ALIAS_LOOKUP.get(normalise(name))


async def compute_sweepstake():
    if not FOOTBALL_DATA_TOKEN:
        raise RuntimeError("FOOTBALL_DATA_TOKEN environment variable not set on the backend")

    async with httpx.AsyncClient() as client:
        matches_data, standings_data = await asyncio.gather(
            fetch_json(client, "/matches"),
            fetch_json(client, "/standings"),
        )

    matches = (matches_data or {}).get("matches", [])

    # Track furthest stage + eliminated flag + record per recognised team
    team_state = {}
    for team in TEAM_TO_PLAYER:
        team_state[team] = {
            "team": team,
            "player": TEAM_TO_PLAYER[team],
            "stage": "GROUP_STAGE",
            "stage_label": STAGE_LABELS["GROUP_STAGE"],
            "eliminated": False,
            "champion": False,
            "played": 0, "won": 0, "drawn": 0, "lost": 0,
            "goals_for": 0, "goals_against": 0, "points": 0,
            "group": None,
        }

    # Fold in group standings (played/won/drawn/lost/points/group letter)
    for group_table in (standings_data or {}).get("standings", []):
        group_name = group_table.get("group")
        for row in group_table.get("table", []):
            row_team_name = (row.get("team") or {}).get("name")
            resolved = resolve_team(row_team_name)
            if resolved and resolved in team_state:
                st = team_state[resolved]
                st["played"] = row.get("playedGames", 0)
                st["won"] = row.get("won", 0)
                st["drawn"] = row.get("draw", 0)
                st["lost"] = row.get("lost", 0)
                st["goals_for"] = row.get("goalsFor", 0)
                st["goals_against"] = row.get("goalsAgainst", 0)
                st["points"] = row.get("points", 0)
                st["group"] = group_name

    # Walk matches in chronological order, advance furthest stage reached,
    # and mark the loser of any finished knockout match as eliminated.
    matches_sorted = sorted(matches, key=lambda m: m.get("utcDate", ""))
    final_match = None

    for m in matches_sorted:
        stage = m.get("stage", "GROUP_STAGE")
        status = m.get("status")
        home = resolve_team((m.get("homeTeam") or {}).get("name", ""))
        away = resolve_team((m.get("awayTeam") or {}).get("name", ""))

        for team_name in (home, away):
            if team_name and team_name in team_state:
                cur_idx = STAGE_ORDER.index(team_state[team_name]["stage"])
                new_idx = STAGE_ORDER.index(stage) if stage in STAGE_ORDER else cur_idx
                if new_idx > cur_idx and not team_state[team_name]["eliminated"]:
                    team_state[team_name]["stage"] = stage
                    team_state[team_name]["stage_label"] = STAGE_LABELS.get(stage, stage)

        if stage != "GROUP_STAGE" and status == "FINISHED" and home and away:
            score = m.get("score", {})
            full_time = score.get("fullTime", {})
            winner = score.get("winner")  # HOME_TEAM / AWAY_TEAM / DRAW
            home_in = home in team_state
            away_in = away in team_state

            loser = None
            if winner == "HOME_TEAM":
                loser = away if away_in else None
            elif winner == "AWAY_TEAM":
                loser = home if home_in else None
            else:
                # Draws shouldn't happen in knockout (penalties resolve it);
                # fall back to penalty shootout winner if present
                pens = score.get("penalties", {})
                if pens.get("homeTeam") is not None and pens.get("awayTeam") is not None:
                    if pens["homeTeam"] > pens["awayTeam"]:
                        loser = away if away_in else None
                    elif pens["awayTeam"] > pens["homeTeam"]:
                        loser = home if home_in else None

            if loser and stage != "THIRD_PLACE":
                team_state[loser]["eliminated"] = True

            if stage == "FINAL":
                final_match = m

    # Determine champion from the final, if played
    if final_match and final_match.get("status") == "FINISHED":
        score = final_match.get("score", {})
        winner = score.get("winner")
        home = resolve_team((final_match.get("homeTeam") or {}).get("name", ""))
        away = resolve_team((final_match.get("awayTeam") or {}).get("name", ""))
        champ = None
        if winner == "HOME_TEAM":
            champ = home
        elif winner == "AWAY_TEAM":
            champ = away
        else:
            pens = score.get("penalties", {})
            if pens.get("homeTeam") is not None and pens.get("awayTeam") is not None:
                champ = home if pens["homeTeam"] > pens["awayTeam"] else away
        if champ and champ in team_state:
            team_state[champ]["champion"] = True
            team_state[champ]["eliminated"] = False

    # Build per-player summary
    players_out = {}
    for player, teams in PLAYERS.items():
        team_rows = [team_state[t] for t in teams]
        alive = [t for t in team_rows if not t["eliminated"]]
        champion_team = next((t["team"] for t in team_rows if t["champion"]), None)
        players_out[player] = {
            "name": player,
            "pot_in": POOL_PER_PERSON,
            "teams": team_rows,
            "alive_count": len(alive),
            "eliminated_count": len(team_rows) - len(alive),
            "is_champion": champion_team is not None,
            "champion_team": champion_team,
        }

    total_pool = POOL_PER_PERSON * len(PLAYERS)
    overall_champion_player = next(
        (p for p in players_out.values() if p["is_champion"]), None
    )

    return {
        "generated_at": int(time.time()),
        "total_pool": total_pool,
        "currency": "GBP",
        "winner": overall_champion_player["name"] if overall_champion_player else None,
        "players": players_out,
    }


@app.get("/api/sweepstake")
async def get_sweepstake():
    async with _cache_lock:
        now = time.time()
        if _cache["data"] is None or (now - _cache["ts"]) > CACHE_TTL_SECONDS:
            try:
                data = await compute_sweepstake()
                _cache["data"] = data
                _cache["ts"] = now
            except Exception as e:
                logger.exception("Failed to refresh sweepstake data")
                if _cache["data"] is None:
                    return {"error": str(e)}
        return _cache["data"]


@app.get("/api/health")
async def health():
    return {"status": "ok", "token_configured": bool(FOOTBALL_DATA_TOKEN)}


@app.get("/")
async def root():
    return {"message": "World Cup Sweepstake API. See /api/sweepstake"}

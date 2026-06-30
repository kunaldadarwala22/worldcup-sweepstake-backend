# World Cup 2026 Sweepstake — worldcup.kkmortgagesolutions.com

Same stack as your other tools: FastAPI backend on Railway, static frontend on Cloudflare Pages.

## 1. Get a free football-data.org API key
1. Go to https://www.football-data.org/client/register and sign up (free, instant).
2. Copy your API token from the dashboard.
3. Free tier covers the World Cup competition and allows 10 requests/minute — plenty, since the backend caches results for 2 minutes and only 4 of you will be hitting it.

## 2. Deploy the backend to Railway
1. Push the `backend/` folder to a new GitHub repo, e.g. `kunaldadarwala22/worldcup-sweepstake-backend`.
2. In Railway: New Project → Deploy from GitHub repo → select it.
3. Add an environment variable: `FOOTBALL_DATA_TOKEN` = your token from step 1.
4. Railway will detect the `Procfile` and run `uvicorn main:app`. No other config needed (same as your other FastAPI services).
5. Once deployed, copy the public Railway URL (e.g. `worldcup-sweepstake-production.up.railway.app`). Test it: visit `https://<that-url>/api/health` — should return `{"status":"ok","token_configured":true}`.

## 3. Wire the frontend to your backend
1. Open `frontend/index.html`.
2. Find the line:
   ```
   const API_URL = "https://YOUR-RAILWAY-APP.up.railway.app/api/sweepstake";
   ```
3. Replace with your real Railway URL + `/api/sweepstake`.

## 4. Deploy the frontend to Cloudflare Pages
1. Push `frontend/index.html` to a new GitHub repo, e.g. `kunaldadarwala22/worldcup-sweepstake`.
2. In Cloudflare Pages: Create a project → Connect to Git → select the repo.
3. Build settings: no build command needed (it's a static file) — just set the output directory to `/` (root).
4. Once deployed, go to Custom Domains on the Pages project and add `worldcup.kkmortgagesolutions.com`. Since your domain is already on Cloudflare, the DNS record gets added automatically.

## How the sweepstake logic works
- Group stage: each team's record (P/W/D/L, points) is pulled live from the official World Cup standings.
- Knockout stage: the moment a match is marked FINISHED, the losing team is flagged "eliminated" and disappears down the rankings on their player's card.
- The Final: the winning team's owner gets the gold "Winner" banner across the top of the site and is shown as winning the full pool.
- Note: teams aren't marked eliminated for missing out on qualifying from the group stage (since that depends on best-third-place rankings across all 12 groups) — they're only marked eliminated once they actually lose a knockout match. This keeps the logic reliable rather than guessing at qualification scenarios.

## Updating team assignments
Team-to-player mapping lives in `backend/main.py` in the `PLAYERS` dict near the top. Edit and redeploy if anyone trades or you got a name wrong.

## Costs
- football-data.org: free
- Railway: same Hobby plan you're already on for your other backends
- Cloudflare Pages: free

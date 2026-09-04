# GamerHQ Bot

**Find Games. Find Mates. Play Together.**

GamerHQ is a Discord community bot for game roles and areas, LFG events,
temporary voice channels, streamer spaces and managed command guides.

## Development

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe bot.py
```

Runtime secrets and `gamerhq.db` are deliberately excluded from Git and Docker
images. See [DEPLOY.md](DEPLOY.md), [RELEASE_WORKFLOW.md](RELEASE_WORKFLOW.md) and
[RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) before deploying.

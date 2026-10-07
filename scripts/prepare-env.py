from pathlib import Path
from secrets import token_urlsafe

root = Path(__file__).resolve().parents[1]
target = root / ".env"
if not target.exists():
    source = (root / ".env.example").read_text()
    target.write_text(source.replace("replace-on-setup", token_urlsafe(48)))
    target.chmod(0o600)
    print("Created .env with a development secret.")
else:
    print("Using existing .env without changes.")

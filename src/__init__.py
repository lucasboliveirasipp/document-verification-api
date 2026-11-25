import os
from pathlib import Path
from dotenv import load_dotenv

# Mesma regra de ambiente do src/settings.py (APP_ENV tem precedência sobre ENV).
env = (os.getenv("APP_ENV") or os.getenv("ENV") or "DSV").upper()
secrets_path = Path(f'secrets.{env}')
variables_path = Path(f'variables.{env}')

load_dotenv(dotenv_path=variables_path, override=False)
load_dotenv(dotenv_path=secrets_path, override=True)

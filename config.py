import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or "some_temporary_secret_key"
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or f"sqlite:///{os.path.join(BASE_DIR, 'crypto.db')}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

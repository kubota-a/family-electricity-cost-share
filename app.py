from flask import Flask
from flask_login import LoginManager
from flask_migrate import Migrate
from dotenv import load_dotenv
import os

from models import db, User


# .env から環境変数を読み込む
load_dotenv()

# Flaskアプリ本体
app = Flask(__name__)

# セッションやflashで使う秘密鍵
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

# データベース接続設定
database_url = os.environ.get("DATABASE_URL")
if not database_url:
    raise RuntimeError("DATABASE_URL is not set")

# 古い接続スキームが来た場合の補正
database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}

# SQLAlchemy / Flask-Migrate 初期化
db.init_app(app)
migrate = Migrate(app, db)

# Flask-Login の初期設定
login_manager = LoginManager()
login_manager.init_app(app)

# 未ログイン時のリダイレクト先
login_manager.login_view = "login"

@login_manager.user_loader
def load_user(user_id):
    """セッションのユーザーIDから User を読み込む。"""
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


@app.route("/")
def index():
    return "Family Electricity Share: Hello!"

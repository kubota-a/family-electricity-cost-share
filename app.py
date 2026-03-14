from flask import Flask, render_template, request, redirect, abort, flash, url_for, session
from flask_migrate import Migrate
# from flask_login import (
#     LoginManager,
#     login_user,
#     logout_user,
#     login_required,
# )
import os

# .env から環境変数（environment variable：設定値を外出しした変数）を読み込むための import
from dotenv import load_dotenv


# models.pyからdbとモデルを import
from models import db  # SETUP段階では db だけ。User/Memo/Task は後で追加。　例：from models import db, User, Task


# =========================================
# .env から環境変数を読み込む
# =========================================
load_dotenv()


# =========================================
# Flaskアプリ本体
# =========================================
app = Flask(__name__)

# Render/本番では必ず環境変数で設定する。ローカルは仮でOK。
# session/flash/flask-loginに必要
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")


# =========================================
# データベース設定
# =========================================
database_url = os.environ.get("DATABASE_URL")

if not database_url:    # database_urlがNoneまたは空文字の場合、エラーを出して停止する
    raise RuntimeError("DATABASE_URL is not set")

# postgres → postgresql 補正で接続エラーを予防
database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False


app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}

# DB初期化
db.init_app(app)
migrate = Migrate(app, db)


# =========================================
# 動作確認用ルート
# =========================================
@app.route("/")
def index():
    return "Family Electricity Share: Hello!"
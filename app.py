from flask import Flask, flash, redirect, render_template, request, url_for
from flask_login import LoginManager, login_user
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from dotenv import load_dotenv
from werkzeug.security import check_password_hash
import os

from models import db, User


# .env から環境変数を読み込む
load_dotenv()

# Flaskアプリ本体
app = Flask(__name__)

# セッションやflashで使う秘密鍵
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

# CSRF対策を有効化（フォームのなりすまし送信を防ぐ）
csrf = CSRFProtect(app)

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

# ログイン失敗時は、入力ミス内容に関係なく同一メッセージを返す
INVALID_LOGIN_MESSAGE = "ログインIDまたはパスワードが違います"


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


@app.route("/login", methods=["GET", "POST"])
def login():
    """ログイン画面の表示とログイン処理を行う。"""
    if request.method == "GET":
        return render_template("login.html", login_id="")

    login_id = request.form.get("login_id", "").strip()
    password = request.form.get("password", "")

    # 空欄チェック
    if not login_id or not password:
        flash(INVALID_LOGIN_MESSAGE)
        return render_template("login.html", login_id=login_id)

    user = User.query.filter_by(login_id=login_id).first()

    # ユーザー不存在とパスワード不一致を同一メッセージに統一
    if user is None or not check_password_hash(user.password_hash, password):
        flash(INVALID_LOGIN_MESSAGE)
        return render_template("login.html", login_id=login_id)

    # 認証成功時にログイン状態を作成（遷移先の詳細分岐はStep2-3で実装）
    login_user(user)
    return redirect(url_for("index"))

from flask import Flask, flash, redirect, render_template, request, url_for
from flask_login import LoginManager, current_user, login_required, login_user, logout_user
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from dotenv import load_dotenv
from werkzeug.security import check_password_hash, generate_password_hash
from functools import wraps
from sqlalchemy.exc import IntegrityError
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
# 未ログインで保護ページへアクセスした際のメッセージを日本語化
login_manager.login_message = "ログインしてください"

# ログイン失敗時は、入力ミス内容に関係なく同一メッセージを返す
INVALID_LOGIN_MESSAGE = "ログインIDまたはパスワードが違います"

# ユーザー管理画面の色丸値 -> users.color に保存するカラーコード
THEME_COLOR_MAP = {
    "color01": "#fff0f3",
    "color02": "#f0f4ff",
    "color03": "#f1fcf0",
    "color04": "#fff9f0",
    "color05": "#f2f2f2",
    "color06": "#fdf2ff",
    "color07": "#f2fbff",
    "color08": "#fffcf2",
    "color09": "#f2fff9",
    "color10": "#fff5f2",
}


@login_manager.user_loader
def load_user(user_id):
    """セッションのユーザーIDから User を読み込む。"""
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


def redirect_by_role(user):
    """role に応じて遷移先を分岐する。"""
    # role分岐
    if user.role == "admin":
        return redirect(url_for("admin_top"))
    if user.role == "user":
        return redirect(url_for("user_top"))

    flash("ロール設定が不正です。管理者に連絡してください。")
    return redirect(url_for("login"))


def admin_required(view_func):
    """admin ロールのみ許可する共通デコレーター。"""
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        # 権限不足時は安全に自分のトップへ戻す
        if current_user.role != "admin":
            return redirect_by_role(current_user)
        return view_func(*args, **kwargs)

    return wrapped_view


@app.route("/")
def index():
    return "Family Electricity Share: Hello!"


@app.route("/login", methods=["GET", "POST"])
def login():
    """ログイン画面の表示とログイン処理を行う。"""
    if request.method == "GET":
        # すでにログイン済みなら role に応じてトップへ戻す
        if current_user.is_authenticated:
            return redirect_by_role(current_user)
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

    login_user(user)
    # ログイン成功後は role に応じたトップへ遷移
    return redirect_by_role(user)


@app.route("/logout", methods=["POST"])
def logout():
    """ログアウトしてログイン画面に戻す。"""
    logout_user()
    return redirect(url_for("login"))


@app.route("/user/top")
@login_required
def user_top():
    """ユーザー用トップ画面を表示する。"""
    # admin が user 画面へ来た場合は、自分のトップへ戻す（flashなし）
    if current_user.role != "user":
        return redirect_by_role(current_user)
    return render_template("user_top.html")


@app.route("/admin/top")
@login_required
@admin_required
def admin_top():
    """管理者用トップ画面を表示する。"""
    return render_template("admin_top.html")


@app.route("/admin/users", methods=["GET", "POST"])
@login_required
@admin_required
def admin_users():
    """管理者用のユーザー一覧表示と新規登録を行う。"""
    form_data = {
        "name": "",
        "login_id": "",
        "role": "user",
        "theme_color": "color01",
    }

    if request.method == "POST":
        # 新規登録フォームの入力値を取得
        form_data["name"] = request.form.get("name", "").strip()
        form_data["login_id"] = request.form.get("login_id", "").strip()
        password = request.form.get("password", "")
        form_data["role"] = request.form.get("role", "").strip()
        form_data["theme_color"] = request.form.get("theme_color", "").strip()

        # 必須項目の空欄チェック
        if (
            not form_data["name"]
            or not form_data["login_id"]
            or not password
            or not form_data["role"]
            or not form_data["theme_color"]
        ):
            flash("すべての項目を入力してください。")
        # role は user/admin のみ許可
        elif form_data["role"] not in {"user", "admin"}:
            flash("役割はメンバーまたは管理者を選択してください。")
        # 色丸で選んだ値だけを受け付ける
        elif form_data["theme_color"] not in THEME_COLOR_MAP:
            flash("テーマカラーの選択が不正です。")
        elif User.query.filter_by(login_id=form_data["login_id"]).first() is not None:
            flash("そのIDはすでに使用されています。")
        else:
            # パスワードは平文保存せず、必ずハッシュ化して保存
            new_user = User(
                login_id=form_data["login_id"],
                password_hash=generate_password_hash(password),
                name=form_data["name"],
                role=form_data["role"],
                color=THEME_COLOR_MAP[form_data["theme_color"]],
            )
            db.session.add(new_user)
            db.session.commit()
            flash("新しいシェアメンバーを登録しました。")
            return redirect(url_for("admin_users"))

    # ユーザー管理画面で表示する一覧を取得（古い登録順）
    users = User.query.order_by(User.created_at.asc(), User.id.asc()).all()
    return render_template("admin_users.html", users=users, form_data=form_data)


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required
@admin_required
def admin_user_delete(user_id):
    """管理者がユーザーを削除する。"""
    # 削除対象を取得し、存在しない場合は一覧へ戻す
    target_user = db.session.get(User, user_id)
    if target_user is None:
        flash("削除対象のシェアメンバーが見つかりません。")
        return redirect(url_for("admin_users"))

    # ログイン中のユーザー自身は削除不可（安全のため）
    if target_user.id == current_user.id:
        flash("ログイン中のユーザー自身は削除できません。")
        return redirect(url_for("admin_users"))

    try:
        db.session.delete(target_user)
        db.session.commit()
        flash("シェアメンバーを削除しました。")
    except IntegrityError:
        db.session.rollback()
        flash("関連データがあるため削除できません。")

    return redirect(url_for("admin_users"))


@app.after_request
def add_no_cache_headers(response):
    """ログイン済みレスポンスにキャッシュ抑止ヘッダーを付与する。"""
    if current_user.is_authenticated:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

from flask import Flask, flash, redirect, render_template, request, url_for
from flask_login import LoginManager, current_user, login_required, login_user, logout_user
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from dotenv import load_dotenv
from werkzeug.security import check_password_hash, generate_password_hash
from functools import wraps
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from sqlalchemy.orm import joinedload
from zoneinfo import ZoneInfo
import os

from models import db, AppSettings, Device, DeviceUsageLog, FinalizedBillMember, User


# .env から環境変数を読み込む
load_dotenv()

# Flaskアプリ本体
app = Flask(__name__)

# セッションやflashで使う秘密鍵
secret_key = os.environ.get("SECRET_KEY")
if not secret_key:
    # Render本番では未設定のまま起動しない（ローカル開発時のみ仮キー許可）
    if os.environ.get("RENDER"):
        raise RuntimeError("SECRET_KEY is not set")
    secret_key = "dev-secret-key-change-me"
app.secret_key = secret_key

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

# 機器管理画面の色丸値 -> devices.color に保存するカラーコード
DEVICE_THEME_COLOR_MAP = {
    "c1": "#ff9999",
    "c2": "#ff99cc",
    "c3": "#ffcc99",
    "c4": "#87ceeb",
    "c5": "#3377ff",
    "c6": "#adc2ff",
    "c7": "#b380ff",
    "c8": "#c0c0c0",
}

# 手動入力(datetime-local)は日本時間として解釈する
TOKYO_TIMEZONE = ZoneInfo("Asia/Tokyo")


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


def parse_datetime_local_as_utc(value):
    """datetime-local文字列を日本時間として受け取り、UTCのaware datetimeへ変換する。"""
    naive_dt = datetime.fromisoformat(value)
    tokyo_aware_dt = naive_dt.replace(tzinfo=TOKYO_TIMEZONE)
    return tokyo_aware_dt.astimezone(timezone.utc)


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

    # ログイン中ユーザーの所有機器だけを取得する
    owned_devices = (
        Device.query
        .filter(Device.user_id == current_user.id)
        .order_by(Device.id.asc())
        .all()
    )

    # 自分の所有機器に紐づく「運転中(end_timeがNULL)」レコードを確認する
    running_log = (
        DeviceUsageLog.query
        .join(Device, DeviceUsageLog.device_id == Device.id)
        .filter(
            Device.user_id == current_user.id,
            DeviceUsageLog.end_time.is_(None),
        )
        .order_by(DeviceUsageLog.start_time.desc(), DeviceUsageLog.id.desc())
        .first()
    )

    # 運転中レコードがあるときは「運転中トップ」を表示する
    if running_log is not None:
        # 仮単価はアプリ全体設定の先頭1件から取得する（未設定ならNone）
        app_settings = AppSettings.query.order_by(AppSettings.id.asc()).first()
        estimated_unit_price = app_settings.estimated_unit_price if app_settings is not None else None

        # DB時刻がタイムゾーンなしで返る環境でも計算が崩れないよう補正する
        running_start_time = running_log.start_time
        if running_start_time.tzinfo is None:
            running_start_time = datetime(
                running_start_time.year,
                running_start_time.month,
                running_start_time.day,
                running_start_time.hour,
                running_start_time.minute,
                running_start_time.second,
                running_start_time.microsecond,
                tzinfo=timezone.utc,
            )

        return render_template(
            "user_top_running.html",
            user_name=current_user.name,
            running_device_name=running_log.device.name,
            running_device_color=running_log.device.color,
            running_start_time_iso=running_start_time.isoformat(),
            running_device_power_kw=float(running_log.device.power_kw),
            estimated_unit_price=float(estimated_unit_price) if estimated_unit_price is not None else None,
        )

    # 運転中レコードがないときは、所有機器一覧つきの通常トップを表示する
    return render_template(
        "user_top_idle.html",
        user_name=current_user.name,
        devices=owned_devices,
    )


@app.route("/user/usage/start", methods=["POST"])
@login_required
def user_usage_start():
    """ユーザーが自分の機器の運転を開始する。"""
    if current_user.role != "user":
        return redirect_by_role(current_user)

    device_id_raw = request.form.get("device_id")
    try:
        device_id = int(device_id_raw)
    except (TypeError, ValueError):
        flash("開始対象の機器が不正です。")
        return redirect(url_for("user_top"))

    # 他人の機器を開始できないよう、ログイン中ユーザー所有の機器だけ許可する
    target_device = (
        Device.query
        .filter(
            Device.id == device_id,
            Device.user_id == current_user.id,
        )
        .first()
    )
    if target_device is None:
        flash("開始対象の機器が見つかりません。")
        return redirect(url_for("user_top"))

    # 二重開始防止のため、開始直前にもう一度「自分の運転中」を確認する
    running_log = (
        DeviceUsageLog.query
        .join(Device, DeviceUsageLog.device_id == Device.id)
        .filter(
            Device.user_id == current_user.id,
            DeviceUsageLog.end_time.is_(None),
        )
        .first()
    )
    if running_log is not None:
        flash("すでに運転中の機器があります。停止してから開始してください。")
        return redirect(url_for("user_top"))

    # 運転開始記録を作成する（TIMESTAMP WITH TIME ZONE 前提でUTCを保存）
    new_log = DeviceUsageLog(
        device_id=target_device.id,
        start_time=datetime.now(timezone.utc),
        end_time=None,
    )
    db.session.add(new_log)
    db.session.commit()
    return redirect(url_for("user_top"))


@app.route("/user/usage/stop", methods=["POST"])
@login_required
def user_usage_stop():
    """ユーザーが自分の現在運転中の機器を停止する。"""
    if current_user.role != "user":
        return redirect_by_role(current_user)

    # 停止対象はURL指定せず、サーバー側で「自分の運転中1件」を探す
    running_log = (
        DeviceUsageLog.query
        .join(Device, DeviceUsageLog.device_id == Device.id)
        .filter(
            Device.user_id == current_user.id,
            DeviceUsageLog.end_time.is_(None),
        )
        .order_by(DeviceUsageLog.start_time.desc(), DeviceUsageLog.id.desc())
        .first()
    )

    # 二重停止対策: すでに停止済みならエラーとして通常トップへ戻す
    if running_log is None:
        flash("停止できる運転中の機器が見つかりません。")
        return redirect(url_for("user_top"))

    running_log.end_time = datetime.now(timezone.utc)
    db.session.commit()
    return redirect(url_for("user_top"))


@app.route("/user/usage/new", methods=["GET", "POST"])
@login_required
def user_usage_new():
    """一般ユーザー用の記録新規追加画面を表示する。"""
    # 一般ユーザー限定画面: admin が来た場合はロール別トップへ戻す
    if current_user.role != "user":
        return redirect_by_role(current_user)

    # 使用機器の選択肢は、ログイン中ユーザーの所有機器だけを表示する
    owned_devices = (
        Device.query
        .filter(Device.user_id == current_user.id)
        .order_by(Device.id.asc())
        .all()
    )

    # フォーム再表示時の差し戻し用データ
    form_data = {
        "device_id": "",
        "start_time": "",
        "end_time": "",
    }

    # Step 2: 記録新規作成のPOSTを最小実装する
    if request.method == "POST":
        form_data["device_id"] = request.form.get("device_id", "")
        form_data["start_time"] = request.form.get("start_time", "")
        form_data["end_time"] = request.form.get("end_time", "")

        # 所有機器チェックのためにdevice_idを数値化する
        try:
            device_id = int(form_data["device_id"])
        except (TypeError, ValueError):
            flash("使用機器の指定が不正です。", "danger")
            return render_template("user_usage_new.html", devices=owned_devices, form_data=form_data)

        # 他ユーザー機器での記録作成を防ぐため、所有機器だけを許可する
        target_device = (
            Device.query
            .filter(
                Device.id == device_id,
                Device.user_id == current_user.id,
            )
            .first()
        )
        if target_device is None:
            flash("選択した機器が見つかりません。", "danger")
            return render_template("user_usage_new.html", devices=owned_devices, form_data=form_data)

        # Step 2時点は最低限として、日時文字列をdatetimeへ変換する
        try:
            start_time = parse_datetime_local_as_utc(form_data["start_time"])
            end_time = (
                parse_datetime_local_as_utc(form_data["end_time"])
                if form_data["end_time"]
                else None
            )
        except ValueError:
            flash("日時の形式が不正です。", "danger")
            return render_template("user_usage_new.html", devices=owned_devices, form_data=form_data)

        new_log = DeviceUsageLog(
            device_id=target_device.id,
            start_time=start_time,
            end_time=end_time,
        )

        try:
            db.session.add(new_log)
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash("記録の保存に失敗しました。", "danger")
            return render_template("user_usage_new.html", devices=owned_devices, form_data=form_data)

        flash("新しい記録を追加しました", "success")
        return redirect(url_for("user_usage_logs"))

    # 使用機器の選択肢は、ログイン中ユーザーの所有機器だけを表示する
    return render_template(
        "user_usage_new.html",
        devices=owned_devices,
        form_data=form_data,
    )


@app.route("/user/usage/logs", methods=["GET"])
@login_required
def user_usage_logs():
    """一般ユーザー用の記録一覧画面（Step 1 の最小実装）を表示する。"""
    # 一般ユーザー限定画面: admin が来た場合はロール別トップへ戻す
    if current_user.role != "user":
        return redirect_by_role(current_user)

    return render_template("user_usage_logs.html")


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
        form_data["role"] = request.form.get("role")
        form_data["theme_color"] = request.form.get("theme_color")

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
    # ID指定で削除対象ユーザーを取得し、存在しない場合は一覧へ戻す
    target_user = db.session.get(User, user_id)
    if target_user is None:
        flash("削除対象のシェアメンバーが見つかりません。")
        return redirect(url_for("admin_users"))

    # ログイン中のユーザー自身は削除不可（安全のため）
    if target_user.id == current_user.id:
        flash("ログイン中のユーザー自身は削除できません。")
        return redirect(url_for("admin_users"))

    # 所有機器が1台でもある場合は削除不可
    has_owned_device = (
        Device.query
        .filter(Device.user_id == target_user.id)
        .first()
        is not None
    )
    if has_owned_device:
        flash("登録済み機器があるメンバーは削除できません。")
        return redirect(url_for("admin_users"))

    # 確定済み電気料金の内訳が1件でもある場合は削除不可
    has_finalized_bill_member = (
        FinalizedBillMember.query
        .filter(FinalizedBillMember.user_id == target_user.id)
        .first()
        is not None
    )
    if has_finalized_bill_member:
        flash("確定済み電気料金の内訳データがあるメンバーは削除できません。")
        return redirect(url_for("admin_users"))

    # 運転中(end_time が NULL)の機器記録がある場合は削除不可
    has_running_device = (
        DeviceUsageLog.query
        .join(Device, DeviceUsageLog.device_id == Device.id)
        .filter(
            Device.user_id == target_user.id,
            DeviceUsageLog.end_time.is_(None),
        )
        .first()
        is not None
    )
    if has_running_device:
        flash("運転中の機器があるメンバーは削除できません。")
        return redirect(url_for("admin_users"))

    db.session.delete(target_user)
    db.session.commit()
    flash("シェアメンバーを削除しました。")

    return redirect(url_for("admin_users"))


@app.route("/admin/devices", methods=["GET", "POST"])
@login_required
@admin_required
def admin_devices():
    """管理者用の機器一覧表示と新規登録を行う。"""
    # 新規登録フォームの初期値
    form_data = {
        "name": "",
        "user_id": "",
        "power_kw": "",
        "theme_color": "c1",
    }

    # 使用メンバー選択肢は一般ユーザー(role='user')のみ表示
    users = (
        User.query
        .filter(User.role == "user")
        .order_by(User.created_at.asc(), User.id.asc())
        .all()
    )

    if request.method == "POST":
        # フォーム入力値を取得（エラー時の再表示にも使う）
        form_data["name"] = request.form.get("name", "").strip()
        form_data["user_id"] = request.form.get("user_id")
        form_data["power_kw"] = request.form.get("power_kw", "").strip()
        form_data["theme_color"] = request.form.get("theme_color")

        # 必須入力チェック
        if (
            not form_data["name"]
            or not form_data["user_id"]
            or not form_data["power_kw"]
            or not form_data["theme_color"]
        ):
            flash("すべての項目を入力してください。")
        # 色丸で選択できる値だけを受け付ける
        elif form_data["theme_color"] not in DEVICE_THEME_COLOR_MAP:
            flash("テーマカラーの選択が不正です。")
        else:
            # user_id は数値で受け取り、users に存在するIDかを確認
            try:
                user_id = int(form_data["user_id"])
            except ValueError:
                user_id = None

            target_user = db.session.get(User, user_id) if user_id is not None else None
            # 存在し、かつ一般ユーザー(role='user')のみ登録を許可
            if target_user is None or target_user.role != "user":
                flash("使用メンバーの選択が不正です。")
            else:
                # power_kw は数値として扱い、負数や文字列を除外する
                try:
                    power_kw = Decimal(form_data["power_kw"])
                except InvalidOperation:
                    power_kw = None

                if power_kw is None or power_kw <= 0:
                    flash("消費電力は0より大きい数値で入力してください。")
                else:
                    # 色丸UIの選択値をカラーコードに変換して保存
                    new_device = Device(
                        name=form_data["name"],
                        user_id=target_user.id,
                        power_kw=power_kw,
                        color=DEVICE_THEME_COLOR_MAP[form_data["theme_color"]],
                    )
                    db.session.add(new_device)
                    db.session.commit()
                    flash("新しい機器を登録しました。")
                    return redirect(url_for("admin_devices"))

    # 一覧表示で使用メンバー名も同時に参照するため、関連ユーザーをまとめて取得
    devices = (
        Device.query
        .options(joinedload(Device.user))
        .order_by(Device.id.asc())
        .all()
    )
    return render_template("admin_devices.html", devices=devices, users=users, form_data=form_data)


@app.route("/admin/devices/<int:device_id>/delete", methods=["POST"])
@login_required
@admin_required
def admin_device_delete(device_id):
    """管理者が機器を削除する。"""
    # ID指定で削除対象機器を取得し、存在しない場合は一覧へ戻す
    target_device = db.session.get(Device, device_id)
    if target_device is None:
        flash("削除対象の機器が見つかりません。")
        return redirect(url_for("admin_devices"))

    # 使用記録が1件でもある機器は削除不可
    has_usage_log = (
        DeviceUsageLog.query
        .filter(DeviceUsageLog.device_id == target_device.id)
        .first()
        is not None
    )
    if has_usage_log:
        flash("使用記録がある機器は削除できません。")
        return redirect(url_for("admin_devices"))

    db.session.delete(target_device)
    db.session.commit()
    flash("機器を削除しました。")

    return redirect(url_for("admin_devices"))


@app.after_request
def add_no_cache_headers(response):
    """ログイン済みレスポンスにキャッシュ抑止ヘッダーを付与する。"""
    if current_user.is_authenticated:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

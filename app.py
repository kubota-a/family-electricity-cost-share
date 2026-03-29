from flask import Flask, flash, redirect, render_template, request, url_for, jsonify
from flask_login import LoginManager, current_user, login_required, login_user, logout_user
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from dotenv import load_dotenv
from werkzeug.security import check_password_hash, generate_password_hash
from functools import wraps
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import joinedload
from zoneinfo import ZoneInfo
import os
import re

from models import db, AppSettings, Device, DeviceUsageLog, FinalizedBill, FinalizedBillMember, User


# .env から環境変数を読み込む
load_dotenv()

# Flaskアプリ本体
app = Flask(__name__)

# セッションの署名に使う鍵
secret_key = os.environ.get("SECRET_KEY")
if not secret_key:
    # 本番環境では鍵なしで起動させない（ローカルだけ仮キーを許可）
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

# 古いURL形式(postgres://)が来たときの互換対応
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

# ログイン失敗理由を出し分けず、同じ文言を返して情報漏えいを防ぐ
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
UTC_MIN_AWARE = datetime(1, 1, 1, tzinfo=timezone.utc)


@login_manager.user_loader
def load_user(user_id):
    """セッションのユーザーIDから User を読み込む。"""
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


def redirect_by_role(user):
    """ユーザー種別に合わせて遷移先を決める。"""
    if user.role == "admin":
        return redirect(url_for("admin_top"))
    if user.role == "user":
        return redirect(url_for("user_top"))

    flash("ロール設定が不正です。管理者に連絡してください。", "danger")
    return redirect(url_for("login"))


def admin_required(view_func):
    """管理者だけが使える画面に付ける共通デコレーター。"""
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        # 権限がない場合はエラー画面にせず、ログイン中ユーザーのトップへ戻す
        if current_user.role != "admin":
            return redirect_by_role(current_user)
        return view_func(*args, **kwargs)

    return wrapped_view


def parse_datetime_local_as_utc(value):
    """フォームの日時文字列（日本時間）をUTCの日時に変換する。"""
    naive_dt = datetime.fromisoformat(value)
    tokyo_aware_dt = naive_dt.replace(tzinfo=TOKYO_TIMEZONE)
    return tokyo_aware_dt.astimezone(timezone.utc)


def ensure_utc_aware(dt_value):
    """タイムゾーン情報つきUTC日時として扱える形にそろえる。"""
    if dt_value is None:
        return None
    if dt_value.tzinfo is None:
        raise ValueError("naive datetime is not allowed")
    return dt_value.astimezone(timezone.utc)


def format_datetime_for_jst_display(dt_value):
    """UTC日時を日本時間に直し、画面表示の形式に整える。"""
    utc_aware_dt = ensure_utc_aware(dt_value)
    tokyo_dt = utc_aware_dt.astimezone(TOKYO_TIMEZONE)
    return tokyo_dt.strftime("%Y/%m/%d %H:%M")


def format_datetime_for_jst_input(dt_value):
    """UTC日時をフォーム入力用の日本時間文字列に整える。"""
    utc_aware_dt = ensure_utc_aware(dt_value)
    return utc_aware_dt.astimezone(TOKYO_TIMEZONE).strftime("%Y-%m-%dT%H:%M")


def calculate_estimated_cost_yen(usage_log, estimated_unit_price):
    """終了済み記録の概算料金(円)を四捨五入した整数で返す。"""
    if usage_log.end_time is None or estimated_unit_price is None:
        return None

    start_time_utc = ensure_utc_aware(usage_log.start_time)
    end_time_utc = ensure_utc_aware(usage_log.end_time)
    usage_seconds = Decimal(str((end_time_utc - start_time_utc).total_seconds()))
    usage_hours = usage_seconds / Decimal("3600")
    estimated_cost = Decimal(str(usage_log.device.power_kw)) * usage_hours * estimated_unit_price
    return int(estimated_cost.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_date_for_jst_display(dt_value):
    """UTC日時を日本時間へ変換し、日付(YYYY/MM/DD)で表示する。"""
    utc_aware_dt = ensure_utc_aware(dt_value)
    return utc_aware_dt.astimezone(TOKYO_TIMEZONE).strftime("%Y/%m/%d")


def format_duration_for_display(start_time, end_time):
    """使用時間を「◯時間◯◯分」形式で返す。"""
    if start_time is None or end_time is None:
        return "-"

    start_time_utc = ensure_utc_aware(start_time)
    end_time_utc = ensure_utc_aware(end_time)
    total_seconds = int((end_time_utc - start_time_utc).total_seconds())
    if total_seconds < 0:
        return "-"

    total_minutes = total_seconds // 60
    hours = total_minutes // 60
    minutes = total_minutes % 60
    if hours == 0:
        return f"{minutes}分"
    return f"{hours}時間{minutes:02d}分"


def parse_date_input(value):
    """date入力(YYYY-MM-DD)を date オブジェクトへ変換する。"""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def parse_decimal_input(value):
    """カンマ付き数値文字列を Decimal に変換する。"""
    normalized = (value or "").replace(",", "").strip()
    if not normalized:
        return None
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def convert_tokyo_date_to_utc_start(date_value):
    """日本時間の日付の00:00:00をUTC aware datetimeへ変換する。"""
    return datetime(
        date_value.year,
        date_value.month,
        date_value.day,
        0,
        0,
        0,
        tzinfo=TOKYO_TIMEZONE,
    ).astimezone(timezone.utc)


def convert_tokyo_date_to_utc_end(date_value):
    """日本時間の日付の23:59:59をUTC aware datetimeへ変換する。"""
    return datetime(
        date_value.year,
        date_value.month,
        date_value.day,
        23,
        59,
        59,
        tzinfo=TOKYO_TIMEZONE,
    ).astimezone(timezone.utc)


def format_decimal_for_display(value):
    """Decimalを表示用の最小表記へ整形する。"""
    if value is None:
        return ""
    text = format(Decimal(str(value)), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_yen_for_display(value):
    """円表示用（カンマ区切り）へ整形する。"""
    yen_value = int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return f"{yen_value:,}円"


def build_bill_preview_cards(user_members, period_start_utc, period_end_utc, unit_price, billing_amount):
    """対象期間の終了済み記録から、メンバー別プレビュー金額を計算する。"""
    if not user_members:
        return None, "一般ユーザーがいないためプレビューを計算できません。", None, None

    member_device_usage_map = {member.id: 0 for member in user_members}

    ended_logs = (
        DeviceUsageLog.query
        .join(Device, DeviceUsageLog.device_id == Device.id)
        .join(User, Device.user_id == User.id)
        .options(joinedload(DeviceUsageLog.device).joinedload(Device.user))
        .filter(DeviceUsageLog.deleted_at.is_(None))
        .filter(DeviceUsageLog.end_time.isnot(None))
        .filter(DeviceUsageLog.start_time >= period_start_utc)
        .filter(DeviceUsageLog.start_time <= period_end_utc)
        .filter(User.role == "user")
        .all()
    )

    for usage_log in ended_logs:
        start_time_utc = ensure_utc_aware(usage_log.start_time)
        end_time_utc = ensure_utc_aware(usage_log.end_time)
        usage_seconds = Decimal(str((end_time_utc - start_time_utc).total_seconds()))
        if usage_seconds <= 0:
            continue

        usage_hours = usage_seconds / Decimal("3600")
        device_usage_amount = Decimal(str(usage_log.device.power_kw)) * usage_hours * unit_price
        rounded_device_usage_amount = int(
            device_usage_amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        member_device_usage_map[usage_log.device.user_id] += rounded_device_usage_amount

    total_device_usage_amount = sum(member_device_usage_map.values())

    equal_share_base = (
        billing_amount - Decimal(total_device_usage_amount)
    ) / Decimal(len(user_members))
    rounded_equal_share_amount = int(
        equal_share_base.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )

    cards = []
    for member in user_members:
        device_usage_amount = member_device_usage_map[member.id]
        share_amount = device_usage_amount + rounded_equal_share_amount
        cards.append(
            {
                "user_id": member.id,
                "member_name": member.name,
                "card_color": member.color,
                "device_usage_amount": device_usage_amount,
                "equal_share_amount": rounded_equal_share_amount,
                "share_amount": share_amount,
            }
        )

    # 端数丸めの影響で合計が請求総額とズレる場合に備えて差分を調整する
    billing_amount_yen = int(billing_amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    total_share_amount = sum(card["share_amount"] for card in cards)
    adjustment = billing_amount_yen - total_share_amount

    if adjustment != 0 and cards:
        max_share_card = max(cards, key=lambda card: (card["share_amount"], -card["user_id"]))
        max_share_card["share_amount"] += adjustment

    cards.sort(key=lambda card: (-card["share_amount"], card["user_id"]))

    display_cards = [
        {
            "member_name": card["member_name"],
            "card_color": card["card_color"],
            "share_amount_display": format_yen_for_display(card["share_amount"]),
            "device_usage_amount_display": format_yen_for_display(card["device_usage_amount"]),
            "equal_share_amount_display": format_yen_for_display(card["equal_share_amount"]),
        }
        for card in cards
    ]

    return display_cards, None, total_device_usage_amount, cards


def build_empty_bill_preview_members(user_members):
    """未入力時のプレビューカード表示データを作る。"""
    return [
        {
            "member_name": member.name,
            "card_color": "#f2f2f2",
            "share_amount_display": "- - - - -円",
            "device_usage_amount_display": "- - - - -円",
            "equal_share_amount_display": "- - - - -円",
        }
        for member in user_members
    ]


def get_admin_bill_confirm_base_context():
    """電気料金確定画面の共通初期情報を組み立てる。"""
    latest_finalized_bill = (
        FinalizedBill.query
        .order_by(FinalizedBill.period_end.desc(), FinalizedBill.id.desc())
        .first()
    )

    if latest_finalized_bill is not None:
        latest_created_display = format_date_for_jst_display(latest_finalized_bill.created_at)
        latest_period_display = (
            f"{format_date_for_jst_display(latest_finalized_bill.period_start)}"
            f"～{format_date_for_jst_display(latest_finalized_bill.period_end)}利用分"
        )

        latest_period_end_utc = ensure_utc_aware(latest_finalized_bill.period_end)
        fixed_period_start_tokyo = (
            latest_period_end_utc.astimezone(TOKYO_TIMEZONE)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            + timedelta(days=1)
        )
        unfinalized_start_display = fixed_period_start_tokyo.strftime("%Y/%m/%d")
        unfinalized_notice_message = f"{unfinalized_start_display}以降の締め日と電気料金が未確定です。"
        fixed_period_start_input = fixed_period_start_tokyo.strftime("%Y-%m-%d")
        fixed_period_start_utc = fixed_period_start_tokyo.astimezone(timezone.utc)
        is_initial_confirm = False
    else:
        latest_created_display = "- - - - / - - / - -"
        latest_period_display = "- - - - / - - / - - ～ - - - - / - - / - - 利用分"
        unfinalized_start_display = "- - - - / - - / - -"
        unfinalized_notice_message = "初回確定の開始日を入力してください"
        fixed_period_start_input = ""
        fixed_period_start_utc = None
        is_initial_confirm = True

    user_members = (
        User.query
        .filter(User.role == "user")
        .order_by(User.created_at.asc(), User.id.asc())
        .all()
    )

    return {
        "latest_created_display": latest_created_display,
        "latest_period_display": latest_period_display,
        "unfinalized_start_display": unfinalized_start_display,
        "unfinalized_notice_message": unfinalized_notice_message,
        "fixed_period_start_input": fixed_period_start_input,
        "fixed_period_start_utc": fixed_period_start_utc,
        "is_initial_confirm": is_initial_confirm,
        "user_members": user_members,
    }


def calculate_bill_confirm_preview(
    *,
    is_initial_confirm,
    fixed_period_start_utc,
    user_members,
    form_period_start,
    form_period_end,
    form_billing_amount,
    form_base_fee,
    form_usage_kwh,
):
    """入力値を検証し、確定単価とメンバープレビュー表示データを返す。"""
    result = {
        "errors": [],
        "is_ready": False,
        "unit_price_display": "- - 円",
        "modal_period_display": "- - - - / - - / - - ～ - - - - / - - / - -",
        "modal_billing_amount_display": "- - 円",
        "modal_base_fee_display": "- - 円",
        "modal_usage_kwh_display": "- - kWh",
        "modal_unit_price_display": "- - 円/kWh",
        "preview_members": build_empty_bill_preview_members(user_members),
        "save_payload": None,
    }

    # 送信時点でも開始日の基準が変わっていないか、最新データで再確認する
    latest_finalized_bill = (
        FinalizedBill.query
        .order_by(FinalizedBill.period_end.desc(), FinalizedBill.id.desc())
        .first()
    )
    expected_period_start_utc = None
    if latest_finalized_bill is not None:
        latest_period_end_utc = ensure_utc_aware(latest_finalized_bill.period_end)
        expected_period_start_utc = (
            latest_period_end_utc.astimezone(TOKYO_TIMEZONE)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            + timedelta(days=1)
        ).astimezone(timezone.utc)

    now_tokyo_date = datetime.now(timezone.utc).astimezone(TOKYO_TIMEZONE).date()

    period_start_date = parse_date_input(form_period_start) if is_initial_confirm else None
    if is_initial_confirm and not form_period_start:
        result["errors"].append("開始日を入力してください")
    elif is_initial_confirm and period_start_date is None:
        result["errors"].append("開始日の入力形式が正しくありません")
    elif (
        is_initial_confirm
        and period_start_date is not None
        and period_start_date > now_tokyo_date
    ):
        result["errors"].append("開始日に未来の日付は指定できません")

    period_end_date = parse_date_input(form_period_end)
    if not form_period_end:
        result["errors"].append("終了日を入力してください")
    elif period_end_date is None:
        result["errors"].append("終了日の入力形式が正しくありません")
    elif period_end_date > now_tokyo_date:
        result["errors"].append("終了日に未来の日付は指定できません")

    billing_amount = parse_decimal_input(form_billing_amount)
    if not form_billing_amount:
        result["errors"].append("請求総額を入力してください")
    elif billing_amount is None:
        result["errors"].append("請求総額は数値で入力してください")
    elif billing_amount <= 0:
        result["errors"].append("請求総額は0より大きい値を入力してください")

    base_fee = parse_decimal_input(form_base_fee)
    if not form_base_fee:
        result["errors"].append("基本料金を入力してください")
    elif base_fee is None:
        result["errors"].append("基本料金は数値で入力してください")
    elif base_fee < 0:
        result["errors"].append("基本料金は0以上で入力してください")

    usage_kwh = parse_decimal_input(form_usage_kwh)
    if not form_usage_kwh:
        result["errors"].append("使用量を入力してください")
    elif usage_kwh is None:
        result["errors"].append("使用量は数値で入力してください")
    elif usage_kwh <= 0:
        result["errors"].append("使用量は0より大きい値を入力してください")

    if billing_amount is not None and base_fee is not None and base_fee > billing_amount:
        result["errors"].append("基本料金が請求総額を上回る値は入力できません")

    if (
        is_initial_confirm
        and period_start_date is not None
        and period_end_date is not None
        and period_end_date < period_start_date
    ):
        result["errors"].append("終了日は開始日以降の日付を入力してください")

    if is_initial_confirm and expected_period_start_utc is not None:
        result["errors"].append("最新の確定状態が更新されたため、画面を再読み込みしてください")

    period_start_utc = None
    period_end_utc = None
    if not result["errors"] and period_end_date is not None:
        if is_initial_confirm:
            period_start_utc = convert_tokyo_date_to_utc_start(period_start_date)
        else:
            period_start_utc = expected_period_start_utc or fixed_period_start_utc
            if period_start_utc is None:
                result["errors"].append("開始日を確定できません。画面を再読み込みしてください")
        period_end_utc = convert_tokyo_date_to_utc_end(period_end_date)

        if period_start_utc is not None and period_end_utc < period_start_utc:
            result["errors"].append("終了日は開始日以降の日付を入力してください")

    # 途中の期間を飛ばして確定できないようにする
    if (
        not result["errors"]
        and not is_initial_confirm
        and expected_period_start_utc is not None
        and period_start_utc != expected_period_start_utc
    ):
        result["errors"].append("期間を飛ばして確定することはできません")

    if result["errors"]:
        return result

    # 同じ期間がすでに確定済みなら登録しない
    duplicate_bill = (
        FinalizedBill.query
        .filter(FinalizedBill.period_start == period_start_utc)
        .filter(FinalizedBill.period_end == period_end_utc)
        .first()
    )
    if duplicate_bill is not None:
        result["errors"].append("同じ利用期間の電気料金はすでに確定済みです")
        return result

    # 対象期間に運転中の記録が残っている間は確定しない
    has_running_log = (
        DeviceUsageLog.query
        .join(Device, DeviceUsageLog.device_id == Device.id)
        .join(User, Device.user_id == User.id)
        .filter(DeviceUsageLog.deleted_at.is_(None))
        .filter(DeviceUsageLog.end_time.is_(None))
        .filter(DeviceUsageLog.start_time >= period_start_utc)
        .filter(DeviceUsageLog.start_time <= period_end_utc)
        .filter(User.role == "user")
        .first()
        is not None
    )
    if has_running_log:
        result["errors"].append("運転中の機器がある期間の電気料金は確定できません")
        return result

    # 単価は「(請求総額 - 基本料金) / 使用量」で計算し、小数第1位で丸める
    unit_price = ((billing_amount - base_fee) / usage_kwh).quantize(
        Decimal("0.1"),
        rounding=ROUND_HALF_UP,
    )
    result["unit_price_display"] = f"{format(unit_price, '.1f')}円/kWh"

    calculated_cards, preview_error, total_device_usage_amount, raw_member_cards = build_bill_preview_cards(
        user_members=user_members,
        period_start_utc=period_start_utc,
        period_end_utc=period_end_utc,
        unit_price=unit_price,
        billing_amount=billing_amount,
    )
    if preview_error is not None:
        result["errors"].append(preview_error)
        return result

    if billing_amount < Decimal(total_device_usage_amount):
        result["errors"].append("請求総額が機器使用料金合計を下回るため確定できません")
        return result

    result["preview_members"] = calculated_cards
    period_start_display = period_start_utc.astimezone(TOKYO_TIMEZONE).strftime("%Y/%m/%d")
    period_end_display = period_end_utc.astimezone(TOKYO_TIMEZONE).strftime("%Y/%m/%d")
    result["modal_period_display"] = f"{period_start_display} ～ {period_end_display}"
    result["modal_billing_amount_display"] = format_yen_for_display(billing_amount)
    result["modal_base_fee_display"] = format_yen_for_display(base_fee)
    result["modal_usage_kwh_display"] = f"{format_decimal_for_display(usage_kwh)}kWh"
    result["modal_unit_price_display"] = result["unit_price_display"]
    result["save_payload"] = {
        "period_start_utc": period_start_utc,
        "period_end_utc": period_end_utc,
        "billing_amount": billing_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "base_fee": base_fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "usage_kwh": usage_kwh.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP),
        "unit_price": unit_price.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP),
        "member_rows": [
            {
                "user_id": member_card["user_id"],
                "device_usage_amount": Decimal(member_card["device_usage_amount"]).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                ),
                "equal_share_amount": Decimal(member_card["equal_share_amount"]).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                ),
                "share_amount": Decimal(member_card["share_amount"]).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                ),
            }
            for member_card in raw_member_cards
        ],
    }
    result["is_ready"] = True
    return result


# =============================
# ■ 共通：トップ入口
# =============================
@app.route("/")
def index():
    """アプリのトップ入口。ログイン状態とロールに応じて適切な画面へリダイレクトする。"""
    if not current_user.is_authenticated:
        return redirect(url_for("login"))

    return redirect_by_role(current_user)


# =============================
# ■ 認証：ログイン・ログアウト
# =============================
@app.route("/login", methods=["GET", "POST"])
def login():
    """ログイン画面の表示とログイン処理を行う。"""
    if request.method == "GET":
        # すでにログイン済みなら、再ログインさせずに各トップへ戻す
        if current_user.is_authenticated:
            return redirect_by_role(current_user)
        return render_template("login.html", login_id="")

    login_id = request.form.get("login_id", "").strip()
    password = request.form.get("password", "")

    # ID/パスワードの未入力を先に弾く
    if not login_id or not password:
        flash(INVALID_LOGIN_MESSAGE, "danger")
        return render_template("login.html", login_id=login_id)

    user = User.query.filter_by(login_id=login_id).first()

    # 存在しないIDかパスワード違いかを画面上で区別しない
    if user is None or not check_password_hash(user.password_hash, password):
        flash(INVALID_LOGIN_MESSAGE, "danger")
        return render_template("login.html", login_id=login_id)

    login_user(user)
    # ログイン後は権限ごとのトップ画面へ
    return redirect_by_role(user)


@app.route("/logout", methods=["POST"])
def logout():
    """ログアウトしてログイン画面に戻す。"""
    logout_user()
    return redirect(url_for("login"))


# =============================
# ■ 一般ユーザー：トップ画面
# =============================
@app.route("/user/top")
@login_required
def user_top():
    """ユーザー用トップ画面を表示する。"""
    # 管理者が直接この画面に来た場合は、管理者トップへ戻す
    if current_user.role != "user":
        return redirect_by_role(current_user)

    # 自分の機器だけを表示対象にする
    owned_devices = (
        Device.query
        .filter(Device.user_id == current_user.id)
        .order_by(Device.id.asc())
        .all()
    )

    # まだ停止時刻が入っていない記録を「運転中」として探す
    running_log = (
        DeviceUsageLog.query
        .join(Device, DeviceUsageLog.device_id == Device.id)
        .filter(
            Device.user_id == current_user.id,
            DeviceUsageLog.deleted_at.is_(None),
            DeviceUsageLog.end_time.is_(None),
        )
        .order_by(DeviceUsageLog.start_time.desc(), DeviceUsageLog.id.desc())
        .first()
    )

    # 運転中の機器がある場合は、運転中用レイアウトを表示する
    if running_log is not None:
        # 仮単価は設定テーブルの先頭1件を利用（未設定ならNone）
        app_settings = AppSettings.query.order_by(AppSettings.id.asc()).first()
        estimated_unit_price = app_settings.estimated_unit_price if app_settings is not None else None

        # DBの日時はUTCで統一して扱う
        running_start_time = ensure_utc_aware(running_log.start_time)

        return render_template(
            "user_top_running.html",
            user_name=current_user.name,
            running_device_name=running_log.device.name,
            running_device_color=running_log.device.color,
            running_start_time_iso=running_start_time.isoformat(),
            running_device_power_kw=float(running_log.device.power_kw),
            estimated_unit_price=float(estimated_unit_price) if estimated_unit_price is not None else None,
        )

    # 運転中がなければ通常トップを表示する
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
        flash("開始対象の機器が不正です。", "danger")
        return redirect(url_for("user_top"))

    # 他人の機器を開始できないように、所有者を必ず確認する
    target_device = (
        Device.query
        .filter(
            Device.id == device_id,
            Device.user_id == current_user.id,
        )
        .first()
    )
    if target_device is None:
        flash("開始対象の機器が見つかりません。", "danger")
        return redirect(url_for("user_top"))

    # 連打や多重送信でも二重開始しないよう、保存直前に再確認する
    running_log = (
        DeviceUsageLog.query
        .join(Device, DeviceUsageLog.device_id == Device.id)
        .filter(
            Device.user_id == current_user.id,
            DeviceUsageLog.deleted_at.is_(None),
            DeviceUsageLog.end_time.is_(None),
        )
        .first()
    )
    if running_log is not None:
        flash("すでに運転中の機器があります。停止してから開始してください。", "danger")
        return redirect(url_for("user_top"))

    # 開始時刻はUTCで保存する
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

    # 停止対象IDを受け取らず、サーバー側で現在の運転中記録を特定する
    running_log = (
        DeviceUsageLog.query
        .join(Device, DeviceUsageLog.device_id == Device.id)
        .filter(
            Device.user_id == current_user.id,
            DeviceUsageLog.deleted_at.is_(None),
            DeviceUsageLog.end_time.is_(None),
        )
        .order_by(DeviceUsageLog.start_time.desc(), DeviceUsageLog.id.desc())
        .first()
    )

    # すでに停止済みならエラーにして戻す（多重停止を防ぐ）
    if running_log is None:
        flash("停止できる運転中の機器が見つかりません。", "danger")
        return redirect(url_for("user_top"))

    running_log.end_time = datetime.now(timezone.utc)
    db.session.commit()
    flash("新しい記録を追加しました", "success")
    return redirect(url_for("user_usage_logs"))


# =============================
# ■ 一般ユーザー：記録追加画面
# =============================
@app.route("/user/usage/new", methods=["GET", "POST"])
@login_required
def user_usage_new():
    """一般ユーザー用の記録新規追加画面を表示する。"""
    # 一般ユーザー専用画面。管理者は管理者トップへ戻す
    if current_user.role != "user":
        return redirect_by_role(current_user)

    # 選べる機器は自分の所有分だけに絞る
    owned_devices = (
        Device.query
        .filter(Device.user_id == current_user.id)
        .order_by(Device.id.asc())
        .all()
    )

    # エラー時に入力値を再表示するための保持データ
    form_data = {
        "device_id": "",
        "start_time": "",
        "end_time": "",
    }

    # 入力値を検証し、通れば新しい使用記録を保存する
    if request.method == "POST":
        form_data["device_id"] = request.form.get("device_id", "")
        form_data["start_time"] = request.form.get("start_time", "")
        form_data["end_time"] = request.form.get("end_time", "")

        # 必須入力の確認（機器・開始日時）
        if not form_data["device_id"]:
            flash("使用機器を選択してください。", "danger")
            return render_template("user_usage_new.html", devices=owned_devices, form_data=form_data)
        if not form_data["start_time"]:
            flash("運転を開始した日時を入力してください。", "danger")
            return render_template("user_usage_new.html", devices=owned_devices, form_data=form_data)

        # 不正なID文字列を除外するため、先に数値へ変換する
        try:
            device_id = int(form_data["device_id"])
        except (TypeError, ValueError):
            flash("使用機器の指定が不正です。", "danger")
            return render_template("user_usage_new.html", devices=owned_devices, form_data=form_data)

        # 他ユーザーの機器IDを指定されても保存できないようにする
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

        # フォームから来た日時文字列をUTC日時へ変換できるか確認する
        try:
            start_time = parse_datetime_local_as_utc(form_data["start_time"])
        except ValueError:
            flash("運転開始日時の形式が不正です。", "danger")
            return render_template("user_usage_new.html", devices=owned_devices, form_data=form_data)

        if form_data["end_time"]:
            try:
                end_time = parse_datetime_local_as_utc(form_data["end_time"])
            except ValueError:
                flash("運転停止日時の形式が不正です。", "danger")
                return render_template("user_usage_new.html", devices=owned_devices, form_data=form_data)
        else:
            end_time = None

        # 開始・停止ともに未来日時は受け付けない
        now_utc = datetime.now(timezone.utc)
        if start_time > now_utc:
            flash("運転開始日時に未来の日時は指定できません。", "danger")
            return render_template("user_usage_new.html", devices=owned_devices, form_data=form_data)
        if end_time is not None and end_time > now_utc:
            flash("運転停止日時に未来の日時は指定できません。", "danger")
            return render_template("user_usage_new.html", devices=owned_devices, form_data=form_data)

        # 停止時刻がある場合だけ、開始より後かを確認する
        if end_time is not None and start_time >= end_time:
            flash("運転停止日時は運転開始日時より後を指定してください。", "danger")
            return render_template("user_usage_new.html", devices=owned_devices, form_data=form_data)

        # 停止時刻なしで追加する場合は、すでに運転中記録がないか確認する
        if end_time is None:
            has_running_log = (
                DeviceUsageLog.query
                .join(Device, DeviceUsageLog.device_id == Device.id)
                .filter(
                    Device.user_id == current_user.id,
                    DeviceUsageLog.deleted_at.is_(None),
                    DeviceUsageLog.end_time.is_(None),
                )
                .first()
                is not None
            )
            if has_running_log:
                flash("現在運転中の機器があるため、停止日時なしでは追加できません。", "danger")
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
            app.logger.exception("user_usage_new: 記録保存中に例外が発生しました")
            db.session.rollback()
            flash("記録の保存に失敗しました。", "danger")
            return render_template("user_usage_new.html", devices=owned_devices, form_data=form_data)

        flash("新しい記録を追加しました", "success")
        return redirect(url_for("user_usage_logs"))

    return render_template(
        "user_usage_new.html",
        devices=owned_devices,
        form_data=form_data,
    )


# =============================
# ■ 一般ユーザー：使用記録一覧画面
# =============================
@app.route("/user/usage/logs", methods=["GET"])
@login_required
def user_usage_logs():
    """一般ユーザー用の記録一覧画面を表示する。"""
    # 一般ユーザー専用画面。管理者は管理者トップへ戻す
    if current_user.role != "user":
        return redirect_by_role(current_user)

    # 絞り込み候補として自分の機器一覧を取得する
    owned_devices = (
        Device.query
        .filter(Device.user_id == current_user.id)
        .order_by(Device.id.asc())
        .all()
    )
    owned_device_ids = {device.id for device in owned_devices}

    # URLパラメータの機器IDは「自分の機器」のときだけ採用する
    selected_device_id = None
    selected_device_id_raw = request.args.get("device_id", "").strip()
    if selected_device_id_raw:
        try:
            candidate_device_id = int(selected_device_id_raw)
        except ValueError:
            candidate_device_id = None
        if candidate_device_id in owned_device_ids:
            selected_device_id = candidate_device_id

    # 最後に確定した請求の翌日を、未確定期間の開始日とする
    latest_finalized_bill = (
        FinalizedBill.query
        .order_by(FinalizedBill.period_end.desc(), FinalizedBill.id.desc())
        .first()
    )

    if latest_finalized_bill is not None:
        latest_period_end = ensure_utc_aware(latest_finalized_bill.period_end)
        latest_period_end_in_tokyo = latest_period_end.astimezone(TOKYO_TIMEZONE)
        unfinalized_start_tokyo = (
            latest_period_end_in_tokyo
            .replace(hour=0, minute=0, second=0, microsecond=0)
            + timedelta(days=1)
        )
        unfinalized_start_utc = unfinalized_start_tokyo.astimezone(timezone.utc)
        unfinalized_start_display = unfinalized_start_tokyo.strftime("%Y/%m/%d")
    else:
        unfinalized_start_utc = None
        unfinalized_start_display = "初回利用記録"

    # 自分の機器かつ未削除の記録だけを検索対象にする
    usage_logs_query = (
        DeviceUsageLog.query
        .join(Device, DeviceUsageLog.device_id == Device.id)
        .options(joinedload(DeviceUsageLog.device))
        .filter(
            Device.user_id == current_user.id,
            DeviceUsageLog.deleted_at.is_(None),
        )
    )

    # すでに確定済みの期間は一覧に出さない
    if unfinalized_start_utc is not None:
        usage_logs_query = usage_logs_query.filter(DeviceUsageLog.start_time >= unfinalized_start_utc)
    if selected_device_id is not None:
        usage_logs_query = usage_logs_query.filter(DeviceUsageLog.device_id == selected_device_id)

    raw_usage_logs = (
        usage_logs_query
        .order_by(DeviceUsageLog.start_time.desc(), DeviceUsageLog.id.desc())
        .all()
    )

    # テンプレートで使いやすい表示用データへ整形する
    app_settings = AppSettings.query.order_by(AppSettings.id.asc()).first()
    estimated_unit_price = app_settings.estimated_unit_price if app_settings is not None else None

    usage_logs = []
    for usage_log in raw_usage_logs:
        usage_logs.append(
            {
                "id": usage_log.id,
                "device_name": usage_log.device.name,
                "device_color": usage_log.device.color,
                "is_running": usage_log.end_time is None,
                "start_time_display": format_datetime_for_jst_display(usage_log.start_time),
                "end_time_display": (
                    format_datetime_for_jst_display(usage_log.end_time)
                    if usage_log.end_time is not None
                    else None
                ),
                "estimated_cost_yen": calculate_estimated_cost_yen(usage_log, estimated_unit_price),
            }
        )

    # 停止済みの記録だけ概算料金を合計する
    summary_total_yen = sum(
        usage_log["estimated_cost_yen"]
        for usage_log in usage_logs
        if usage_log["estimated_cost_yen"] is not None
    )

    return render_template(
        "user_usage_logs.html",
        owned_devices=owned_devices,
        selected_device_id=selected_device_id,
        usage_logs=usage_logs,
        unfinalized_start_display=unfinalized_start_display,
        summary_total_yen=summary_total_yen,
    )


# =============================
# ■ 一般ユーザー：記録編集画面
# =============================
@app.route("/user/usage/<int:usage_log_id>/edit", methods=["GET", "POST"])
@login_required
def user_usage_edit(usage_log_id):
    """一般ユーザー用の記録編集画面を表示する。"""
    # 一般ユーザー専用画面。管理者は管理者トップへ戻す
    if current_user.role != "user":
        return redirect_by_role(current_user)

    # 編集時に選べる機器も自分の所有分だけにする
    owned_devices = (
        Device.query
        .filter(Device.user_id == current_user.id)
        .order_by(Device.id.asc())
        .all()
    )

    # 最後に確定した請求の翌日を、編集可能な最古日として使う
    latest_finalized_bill = (
        FinalizedBill.query
        .order_by(FinalizedBill.period_end.desc(), FinalizedBill.id.desc())
        .first()
    )
    if latest_finalized_bill is not None:
        latest_period_end = ensure_utc_aware(latest_finalized_bill.period_end)
        latest_period_end_in_tokyo = latest_period_end.astimezone(TOKYO_TIMEZONE)
        unfinalized_start_tokyo = (
            latest_period_end_in_tokyo
            .replace(hour=0, minute=0, second=0, microsecond=0)
            + timedelta(days=1)
        )
        unfinalized_start_utc = unfinalized_start_tokyo.astimezone(timezone.utc)
    else:
        unfinalized_start_utc = None

    # 他人の記録や確定済み期間の記録は編集できないようにする
    usage_log_query = (
        DeviceUsageLog.query
        .join(Device, DeviceUsageLog.device_id == Device.id)
        .options(joinedload(DeviceUsageLog.device))
        .filter(
            DeviceUsageLog.id == usage_log_id,
            Device.user_id == current_user.id,
            DeviceUsageLog.deleted_at.is_(None),
        )
    )
    if unfinalized_start_utc is not None:
        usage_log_query = usage_log_query.filter(DeviceUsageLog.start_time >= unfinalized_start_utc)
    target_usage_log = usage_log_query.first()
    if target_usage_log is None:
        return redirect(url_for("user_usage_logs"))

    def render_edit_with_form(form_data):
        """入力値を保持して編集画面を再表示する。"""
        return render_template(
            "user_usage_edit.html",
            devices=owned_devices,
            form_data=form_data,
            usage_log_id=usage_log_id,
        )

    # 入力値を検証し、問題がなければ使用記録を更新する
    if request.method == "POST":
        form_data = {
            "device_id": request.form.get("device_id", "").strip(),
            "start_time": request.form.get("start_time", "").strip(),
            "end_time": request.form.get("end_time", "").strip(),
        }

        # 必須入力の確認（機器・開始日時）
        if not form_data["device_id"]:
            flash("使用機器を選択してください。", "danger")
            return render_edit_with_form(form_data)
        if not form_data["start_time"]:
            flash("運転を開始した日時を入力してください。", "danger")
            return render_edit_with_form(form_data)

        try:
            device_id = int(form_data["device_id"])
        except (TypeError, ValueError):
            flash("使用機器の指定が不正です。", "danger")
            return render_edit_with_form(form_data)

        # 他人の機器へ付け替えできないよう所有者を確認する
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
            return render_edit_with_form(form_data)

        # 日時文字列をUTC日時へ変換できるか確認する
        try:
            start_time = parse_datetime_local_as_utc(form_data["start_time"])
        except ValueError:
            flash("運転開始日時の形式が不正です。", "danger")
            return render_edit_with_form(form_data)

        if form_data["end_time"]:
            try:
                end_time = parse_datetime_local_as_utc(form_data["end_time"])
            except ValueError:
                flash("運転停止日時の形式が不正です。", "danger")
                return render_edit_with_form(form_data)
        else:
            end_time = None

        # 確定済み期間に入る記録は編集させない
        if unfinalized_start_utc is not None and start_time < unfinalized_start_utc:
            flash("確定済み期間の記録は編集できません。", "danger")
            return render_edit_with_form(form_data)

        # 開始・停止ともに未来日時は受け付けない
        now_utc = datetime.now(timezone.utc)
        if start_time > now_utc:
            flash("運転開始日時に未来の日時は指定できません。", "danger")
            return render_edit_with_form(form_data)
        if end_time is not None and end_time > now_utc:
            flash("運転停止日時に未来の日時は指定できません。", "danger")
            return render_edit_with_form(form_data)

        # 停止時刻がある場合だけ、開始より後かを確認する
        if end_time is not None and start_time >= end_time:
            flash("運転停止日時は運転開始日時より後を指定してください。", "danger")
            return render_edit_with_form(form_data)

        # 停止時刻なしで更新する場合、他に運転中記録がないか確認する
        if end_time is None:
            has_running_log = (
                DeviceUsageLog.query
                .join(Device, DeviceUsageLog.device_id == Device.id)
                .filter(
                    Device.user_id == current_user.id,
                    DeviceUsageLog.deleted_at.is_(None),
                    DeviceUsageLog.end_time.is_(None),
                    DeviceUsageLog.id != target_usage_log.id,
                )
                .first()
                is not None
            )
            if has_running_log:
                flash("現在運転中の機器があるため、停止日時なしでは更新できません。", "danger")
                return render_edit_with_form(form_data)

        target_usage_log.device_id = target_device.id
        target_usage_log.start_time = start_time
        target_usage_log.end_time = end_time

        try:
            db.session.commit()
        except Exception:
            app.logger.exception("user_usage_edit: 記録更新中に例外が発生しました")
            db.session.rollback()
            flash("記録の更新に失敗しました。", "danger")
            return render_edit_with_form(form_data)

        flash("記録を更新しました", "success")
        return redirect(url_for("user_usage_logs"))

    form_data = {
        "device_id": str(target_usage_log.device_id),
        "start_time": format_datetime_for_jst_input(target_usage_log.start_time),
        "end_time": (
            format_datetime_for_jst_input(target_usage_log.end_time)
            if target_usage_log.end_time is not None
            else ""
        ),
    }

    return render_template(
        "user_usage_edit.html",
        devices=owned_devices,
        form_data=form_data,
        usage_log_id=usage_log_id,
    )


# =============================
# ■ 一般ユーザー：記録削除画面
# =============================
@app.route("/user/usage/<int:usage_log_id>/delete", methods=["GET", "POST"])
@login_required
def user_usage_delete(usage_log_id):
    """一般ユーザー用の記録削除画面を表示する。"""
    # 一般ユーザー専用画面。管理者は管理者トップへ戻す
    if current_user.role != "user":
        return redirect_by_role(current_user)

    # 最後に確定した請求の翌日を、削除可能な最古日として使う
    latest_finalized_bill = (
        FinalizedBill.query
        .order_by(FinalizedBill.period_end.desc(), FinalizedBill.id.desc())
        .first()
    )
    if latest_finalized_bill is not None:
        latest_period_end = ensure_utc_aware(latest_finalized_bill.period_end)
        latest_period_end_in_tokyo = latest_period_end.astimezone(TOKYO_TIMEZONE)
        unfinalized_start_tokyo = (
            latest_period_end_in_tokyo
            .replace(hour=0, minute=0, second=0, microsecond=0)
            + timedelta(days=1)
        )
        unfinalized_start_utc = unfinalized_start_tokyo.astimezone(timezone.utc)
    else:
        unfinalized_start_utc = None

    # 画面表示(GET)と削除実行(POST)で同じ検索条件を使い回す
    def find_target_usage_log():
        usage_log_query = (
            DeviceUsageLog.query
            .join(Device, DeviceUsageLog.device_id == Device.id)
            .options(joinedload(DeviceUsageLog.device))
            .filter(
                DeviceUsageLog.id == usage_log_id,
                Device.user_id == current_user.id,
                DeviceUsageLog.deleted_at.is_(None),
            )
        )
        if unfinalized_start_utc is not None:
            usage_log_query = usage_log_query.filter(DeviceUsageLog.start_time >= unfinalized_start_utc)
        return usage_log_query.first()

    # 他人の記録や確定済み期間の記録は削除できないようにする
    target_usage_log = find_target_usage_log()
    if target_usage_log is None:
        return redirect(url_for("user_usage_logs"))

    if request.method == "POST":
        # 物理削除はせず、削除日時を入れて論理削除にする
        target_usage_log.deleted_at = datetime.now(timezone.utc)
        try:
            db.session.commit()
        except Exception:
            app.logger.exception("user_usage_delete: 記録削除中に例外が発生しました")
            db.session.rollback()
            flash("記録の削除に失敗しました。", "danger")
        else:
            flash("記録を削除しました", "success")
            return redirect(url_for("user_usage_logs"))

    # 一覧と表示がずれないよう、削除確認画面でも同じ形式に整える
    app_settings = AppSettings.query.order_by(AppSettings.id.asc()).first()
    estimated_unit_price = app_settings.estimated_unit_price if app_settings is not None else None
    usage_log = {
        "id": target_usage_log.id,
        "device_name": target_usage_log.device.name,
        "device_color": target_usage_log.device.color,
        "is_running": target_usage_log.end_time is None,
        "start_time_display": format_datetime_for_jst_display(target_usage_log.start_time),
        "end_time_display": (
            format_datetime_for_jst_display(target_usage_log.end_time)
            if target_usage_log.end_time is not None
            else None
        ),
        "estimated_cost_yen": calculate_estimated_cost_yen(target_usage_log, estimated_unit_price),
    }

    return render_template("user_usage_delete.html", usage_log=usage_log)


# =============================
# ■ 一般ユーザー：シェア金額一覧画面
# =============================
@app.route("/user/share-amounts", methods=["GET"])
@login_required
def user_share_amounts():
    """一般ユーザー用のシェア金額一覧画面を表示する。"""
    # 一般ユーザー限定画面: admin が来た場合はロール別トップへ戻す
    if current_user.role != "user":
        return redirect_by_role(current_user)

    # ログイン中ユーザーの内訳がある請求のみを period_end 降順で取得する
    member_rows = (
        FinalizedBillMember.query
        .join(FinalizedBill, FinalizedBillMember.finalized_bill_id == FinalizedBill.id)
        .options(joinedload(FinalizedBillMember.finalized_bill))
        .filter(FinalizedBillMember.user_id == current_user.id)
        .order_by(FinalizedBill.period_end.desc(), FinalizedBill.id.desc())
        .all()
    )

    # 画面表示向けのカード情報を作る（対象はユーザー内訳がある請求のみ）
    bill_cards = []
    for member_row in member_rows:
        if member_row.finalized_bill is None:
            continue

        finalized_bill = member_row.finalized_bill
        bill_cards.append(
            {
                "bill_id": finalized_bill.id,
                "period_range_display": (
                    f"{format_date_for_jst_display(finalized_bill.period_start)}〜"
                    f"{format_date_for_jst_display(finalized_bill.period_end)}"
                ),
                "period_display": (
                    f"{format_date_for_jst_display(finalized_bill.period_start)}〜"
                    f"{format_date_for_jst_display(finalized_bill.period_end)} 利用分"
                ),
                "share_amount_display": format_yen_for_display(member_row.share_amount),
            }
        )

    # 安全側の空判定: 表示対象カードが1件もない場合は空状態として扱う
    has_records = len(bill_cards) > 0
    latest_record = bill_cards[0] if has_records else None
    history_records = bill_cards[1:] if has_records else []

    return render_template(
        "user_share_amounts.html",
        has_records=has_records,
        latest_record=latest_record,
        history_records=history_records,
    )


# =============================
# ■ 一般ユーザー：シェア金額詳細画面
# =============================
@app.route("/user/share-amounts/<finalized_bill_id>", methods=["GET"])
@login_required
def user_share_amount_detail(finalized_bill_id):
    """一般ユーザー用のシェア金額詳細画面（MVP仕上げ）を表示する。"""
    # 一般ユーザー限定画面: admin が来た場合はロール別トップへ戻す
    if current_user.role != "user":
        return redirect_by_role(current_user)

    # 不正ID（数値以外）が来た場合も画面エラーにせず一覧へ戻す
    try:
        finalized_bill_id_int = int(finalized_bill_id)
    except (TypeError, ValueError):
        return redirect(url_for("user_share_amounts"))

    # 所有権チェック: ログイン中ユーザーの内訳がある請求のみ表示を許可する
    target_member = (
        FinalizedBillMember.query
        .options(joinedload(FinalizedBillMember.finalized_bill))
        .filter(
            FinalizedBillMember.finalized_bill_id == finalized_bill_id_int,
            FinalizedBillMember.user_id == current_user.id,
        )
        .first()
    )
    if target_member is None or target_member.finalized_bill is None:
        return redirect(url_for("user_share_amounts"))

    target_bill = target_member.finalized_bill

    # 一覧から受け取った対象IDをもとに、詳細表示用データを作る
    period_start_display = (
        format_date_for_jst_display(target_bill.period_start)
        if target_bill.period_start is not None
        else "- - - - / - - / - -"
    )
    period_end_display = (
        format_date_for_jst_display(target_bill.period_end)
        if target_bill.period_end is not None
        else "- - - - / - - / - -"
    )

    detail_view_data = {
        "bill_id": finalized_bill_id_int,
        "period_display": f"{period_start_display}〜{period_end_display}",
        "confirmed_date_display": (
            format_date_for_jst_display(target_bill.created_at)
            if target_bill.created_at is not None
            else "- - - - / - - / - -"
        ),
        "total_electricity_display": (
            format_yen_for_display(target_bill.billing_amount)
            if target_bill.billing_amount is not None
            else "- 円"
        ),
        "share_amount_display": (
            format_yen_for_display(target_member.share_amount)
            if target_member.share_amount is not None
            else "- 円"
        ),
        "device_usage_amount_display": (
            format_yen_for_display(target_member.device_usage_amount)
            if target_member.device_usage_amount is not None
            else "- 円"
        ),
        "equal_share_amount_display": (
            format_yen_for_display(target_member.equal_share_amount)
            if target_member.equal_share_amount is not None
            else "- 円"
        ),
        "unit_price_display": (
            format_decimal_for_display(target_bill.unit_price)
            if target_bill.unit_price is not None
            else "-"
        ),
    }

    return render_template(
        "user_share_amount_detail.html",
        detail=detail_view_data,
    )


# =============================
# ■ 管理者：トップ画面
# =============================
@app.route("/admin/top", methods=["GET", "POST"])
@login_required
@admin_required
def admin_top():
    """管理者用トップ画面を表示する。"""
    selected_price_mode = "manual"
    manual_input_override = None

    # 仮単価更新の入力を受け取り、更新方法ごとに検証する
    if request.method == "POST":
        has_post_error = False
        update_mode = request.form.get("estimated_price_mode", "manual")
        selected_price_mode = update_mode
        manual_price_raw = request.form.get("estimated_unit_price", "").strip()
        manual_input_override = manual_price_raw

        if update_mode == "latest_three_average":
            latest_three_for_update = (
                FinalizedBill.query
                .order_by(FinalizedBill.period_end.desc(), FinalizedBill.id.desc())
                .limit(3)
                .all()
            )
            if not latest_three_for_update:
                flash("確定済み電気料金がないため、直近3件平均では更新できません。", "danger")
                has_post_error = True
            else:
                unit_price_sum = sum(
                    Decimal(str(finalized_bill.unit_price))
                    for finalized_bill in latest_three_for_update
                )
                new_estimated_unit_price = unit_price_sum / Decimal(len(latest_three_for_update))
        elif update_mode == "manual":
            if not manual_price_raw:
                flash("任意の金額を入力してください。", "danger")
                has_post_error = True

            # 任意の金額は「半角数字 + 小数第1位まで」のみ許可する
            elif re.fullmatch(r"\d+(\.\d)?", manual_price_raw) is None:
                flash("任意の金額は小数第1位までの数値で入力してください。", "danger")
                has_post_error = True
            else:
                try:
                    new_estimated_unit_price = Decimal(manual_price_raw)
                except InvalidOperation:
                    flash("任意の金額は数値で入力してください。", "danger")
                    has_post_error = True

                if not has_post_error and new_estimated_unit_price <= 0:
                    flash("任意の金額は0より大きい値を入力してください。", "danger")
                    has_post_error = True
        else:
            flash("仮単価の更新方法が不正です。", "danger")
            has_post_error = True

        if not has_post_error:
            app_settings = AppSettings.query.order_by(AppSettings.id.asc()).first()
            if app_settings is None:
                app_settings = AppSettings(estimated_unit_price=new_estimated_unit_price)
                db.session.add(app_settings)
            else:
                app_settings.estimated_unit_price = new_estimated_unit_price

            try:
                db.session.commit()
            except Exception:
                app.logger.exception("admin_top: 仮単価更新時に例外が発生しました")
                db.session.rollback()
                flash("仮単価の更新に失敗しました。", "danger")
            else:
                flash("仮単価を更新しました。", "success")
                return redirect(url_for("admin_top"))

    # 最新の確定済み電気料金を取得し、未確定期間の開始日時を算出する
    latest_finalized_bill = (
        FinalizedBill.query
        .order_by(FinalizedBill.period_end.desc(), FinalizedBill.id.desc())
        .first()
    )
    if latest_finalized_bill is not None:
        latest_created_display = format_date_for_jst_display(latest_finalized_bill.created_at)
        latest_period_display = (
            f"{format_date_for_jst_display(latest_finalized_bill.period_start)}"
            f"〜{format_date_for_jst_display(latest_finalized_bill.period_end)}利用分"
        )

        latest_period_end_utc = ensure_utc_aware(latest_finalized_bill.period_end)
        unfinalized_start_tokyo = (
            latest_period_end_utc.astimezone(TOKYO_TIMEZONE)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            + timedelta(days=1)
        )
        unfinalized_start_utc = unfinalized_start_tokyo.astimezone(timezone.utc)
        unfinalized_start_display = unfinalized_start_tokyo.strftime("%Y/%m/%d")
        unfinalized_start_date_tokyo = unfinalized_start_tokyo.date()
    else:
        latest_created_display = "- - - - -"
        latest_period_display = "- - - - -"
        unfinalized_start_utc = None
        unfinalized_start_display = "- - - - -"
        unfinalized_start_date_tokyo = None

    # 管理者アカウントは集計対象に含めず、一般ユーザーのみ表示する
    user_members = (
        User.query
        .filter(User.role == "user")
        .order_by(User.created_at.asc(), User.id.asc())
        .all()
    )
    user_member_ids = {member.id for member in user_members}

    # 未確定記録一覧の絞り込み入力値を受け取る
    selected_member_id = request.args.get("member_id", "").strip()
    filter_start_date = request.args.get("start_date", "").strip()
    filter_end_date = request.args.get("end_date", "").strip()
    selected_member_id_int = None
    filter_start_utc = None
    filter_end_utc = None
    has_filter_error = False

    now_tokyo = datetime.now(timezone.utc).astimezone(TOKYO_TIMEZONE)
    today_tokyo_date = now_tokyo.date()

    if selected_member_id:
        try:
            selected_member_id_int = int(selected_member_id)
        except ValueError:
            flash("メンバー絞り込みの指定が不正です。", "danger")
            has_filter_error = True
            selected_member_id_int = None
        else:
            if selected_member_id_int not in user_member_ids:
                flash("メンバー絞り込みの指定が不正です。", "danger")
                has_filter_error = True
                selected_member_id_int = None

    filter_start_date_obj = None
    filter_end_date_obj = None
    if filter_start_date:
        try:
            filter_start_date_obj = datetime.strptime(filter_start_date, "%Y-%m-%d").date()
        except ValueError:
            flash("開始日の形式が不正です。", "danger")
            has_filter_error = True
    if filter_end_date:
        try:
            filter_end_date_obj = datetime.strptime(filter_end_date, "%Y-%m-%d").date()
        except ValueError:
            flash("終了日の形式が不正です。", "danger")
            has_filter_error = True

    # 絞り込み条件が未確定期間の範囲内かをサーバー側でも確認する
    if filter_start_date_obj is not None and unfinalized_start_date_tokyo is not None:
        if filter_start_date_obj < unfinalized_start_date_tokyo:
            flash("開始日は未確定期間の開始日以降を指定してください。", "danger")
            has_filter_error = True
    if filter_end_date_obj is not None and unfinalized_start_date_tokyo is not None:
        if filter_end_date_obj < unfinalized_start_date_tokyo:
            flash("終了日は未確定期間の開始日以降を指定してください。", "danger")
            has_filter_error = True
    if filter_start_date_obj is not None and filter_start_date_obj > today_tokyo_date:
        flash("開始日は本日以前を指定してください。", "danger")
        has_filter_error = True
    if filter_end_date_obj is not None and filter_end_date_obj > today_tokyo_date:
        flash("終了日は本日以前を指定してください。", "danger")
        has_filter_error = True
    if filter_start_date_obj is not None and filter_end_date_obj is not None:
        if filter_start_date_obj > filter_end_date_obj:
            flash("開始日は終了日以前を指定してください。", "danger")
            has_filter_error = True

    if not has_filter_error:
        if filter_start_date_obj is not None:
            start_tokyo_dt = datetime(
                filter_start_date_obj.year,
                filter_start_date_obj.month,
                filter_start_date_obj.day,
                0,
                0,
                0,
                tzinfo=TOKYO_TIMEZONE,
            )
            filter_start_utc = start_tokyo_dt.astimezone(timezone.utc)
        if filter_end_date_obj is not None:
            end_tokyo_dt = datetime(
                filter_end_date_obj.year,
                filter_end_date_obj.month,
                filter_end_date_obj.day,
                23,
                59,
                59,
                tzinfo=TOKYO_TIMEZONE,
            )
            filter_end_utc = end_tokyo_dt.astimezone(timezone.utc)

    # 画面に出す仮単価は設定テーブルの先頭1件を使う
    app_settings = AppSettings.query.order_by(AppSettings.id.asc()).first()
    estimated_unit_price = app_settings.estimated_unit_price if app_settings is not None else None
    if estimated_unit_price is not None:
        current_estimated_unit_price_display = str(
            Decimal(str(estimated_unit_price)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        )
        current_estimated_unit_price_input = str(
            Decimal(str(estimated_unit_price)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        )
    else:
        current_estimated_unit_price_display = "- - -"
        current_estimated_unit_price_input = ""

    if manual_input_override is not None:
        current_estimated_unit_price_input = manual_input_override

    # UI表示用に、直近3件の確定単価の平均を計算する
    latest_three_finalized_bills = (
        FinalizedBill.query
        .order_by(FinalizedBill.period_end.desc(), FinalizedBill.id.desc())
        .limit(3)
        .all()
    )
    if latest_three_finalized_bills:
        total_unit_price = sum(
            Decimal(str(finalized_bill.unit_price))
            for finalized_bill in latest_three_finalized_bills
        )
        average_unit_price = total_unit_price / Decimal(len(latest_three_finalized_bills))
        latest_three_avg_unit_price_display = str(
            average_unit_price.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        )
    else:
        latest_three_avg_unit_price_display = "- - -"

    # 未確定期間の「停止済み記録」だけをメンバーごとに集計する
    ended_logs_query = (
        DeviceUsageLog.query
        .join(Device, DeviceUsageLog.device_id == Device.id)
        .join(User, Device.user_id == User.id)
        .options(joinedload(DeviceUsageLog.device).joinedload(Device.user))
        .filter(
            User.role == "user",
            DeviceUsageLog.deleted_at.is_(None),
            DeviceUsageLog.end_time.isnot(None),
        )
    )
    if unfinalized_start_utc is not None:
        ended_logs_query = ended_logs_query.filter(DeviceUsageLog.start_time >= unfinalized_start_utc)
    ended_logs = (
        ended_logs_query
        .order_by(DeviceUsageLog.start_time.desc(), DeviceUsageLog.id.desc())
        .all()
    )

    member_summaries = {}
    for member in user_members:
        member_summaries[member.id] = {
            "user": member,
            "total_estimated_cost_yen": 0,
            "latest_start_time": None,
            "has_cost_data": estimated_unit_price is not None,
        }

    for usage_log in ended_logs:
        owner_user = usage_log.device.user
        if owner_user is None or owner_user.id not in member_summaries:
            continue

        member_summary = member_summaries[owner_user.id]
        estimated_cost_yen = calculate_estimated_cost_yen(usage_log, estimated_unit_price)
        if estimated_cost_yen is None:
            member_summary["has_cost_data"] = False
        else:
            member_summary["total_estimated_cost_yen"] += estimated_cost_yen

        current_latest = member_summary["latest_start_time"]
        usage_start_utc = ensure_utc_aware(usage_log.start_time)
        if current_latest is None or usage_start_utc > current_latest:
            member_summary["latest_start_time"] = usage_start_utc

    member_estimate_cards = []
    for member_summary in member_summaries.values():
        member = member_summary["user"]
        if member_summary["has_cost_data"]:
            amount_display = f"{member_summary['total_estimated_cost_yen']:,}円"
        else:
            amount_display = "- - -"

        member_estimate_cards.append(
            {
                "user_id": member.id,
                "name": member.name,
                "amount_display": amount_display,
                "latest_start_time": member_summary["latest_start_time"],
                "color": member.color,
            }
        )

    # 最近使っているメンバーが上に来るように並べ替える
    member_estimate_cards.sort(
        key=lambda item: (
            item["latest_start_time"] is not None,
            item["latest_start_time"] or UTC_MIN_AWARE,
        ),
        reverse=True,
    )

    # 管理画面では状態確認のため、論理削除済みも一覧に含める
    unfinalized_logs_query = (
        DeviceUsageLog.query
        .join(Device, DeviceUsageLog.device_id == Device.id)
        .join(User, Device.user_id == User.id)
        .options(joinedload(DeviceUsageLog.device).joinedload(Device.user))
        .filter(User.role == "user")
    )
    if unfinalized_start_utc is not None:
        unfinalized_logs_query = unfinalized_logs_query.filter(DeviceUsageLog.start_time >= unfinalized_start_utc)
    if not has_filter_error and selected_member_id_int is not None:
        unfinalized_logs_query = unfinalized_logs_query.filter(Device.user_id == selected_member_id_int)
    if not has_filter_error and filter_start_utc is not None:
        unfinalized_logs_query = unfinalized_logs_query.filter(DeviceUsageLog.start_time >= filter_start_utc)
    if not has_filter_error and filter_end_utc is not None:
        unfinalized_logs_query = unfinalized_logs_query.filter(DeviceUsageLog.start_time <= filter_end_utc)

    raw_unfinalized_logs = (
        unfinalized_logs_query
        .order_by(DeviceUsageLog.start_time.desc(), DeviceUsageLog.id.desc())
        .all()
    )

    unfinalized_usage_logs = []
    for usage_log in raw_unfinalized_logs:
        if usage_log.deleted_at is not None:
            status_type = "deleted"
        elif usage_log.end_time is None:
            status_type = "running"
        else:
            status_type = "normal"

        if usage_log.end_time is None:
            duration_display = "-"
            estimated_cost_display = "-"
        else:
            duration_display = format_duration_for_display(usage_log.start_time, usage_log.end_time)
            estimated_cost_yen = calculate_estimated_cost_yen(usage_log, estimated_unit_price)
            estimated_cost_display = f"{estimated_cost_yen:,}円" if estimated_cost_yen is not None else "-"

        unfinalized_usage_logs.append(
            {
                "id": usage_log.id,
                "start_time_display": format_datetime_for_jst_display(usage_log.start_time),
                "member_name": usage_log.device.user.name,
                "device_name": usage_log.device.name,
                "duration_display": duration_display,
                "estimated_cost_display": estimated_cost_display,
                "status_type": status_type,
            }
        )

    return render_template(
        "admin_top.html",
        latest_created_display=latest_created_display,
        latest_period_display=latest_period_display,
        unfinalized_start_display=unfinalized_start_display,
        current_estimated_unit_price_display=current_estimated_unit_price_display,
        current_estimated_unit_price_input=current_estimated_unit_price_input,
        latest_three_avg_unit_price_display=latest_three_avg_unit_price_display,
        user_members=user_members,
        member_estimate_cards=member_estimate_cards,
        unfinalized_usage_logs=unfinalized_usage_logs,
        selected_member_id=selected_member_id,
        filter_start_date=filter_start_date,
        filter_end_date=filter_end_date,
        selected_price_mode=selected_price_mode,
    )


# =============================
# ■ 管理者：シェアメンバー管理
# =============================
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
        # 登録フォームから入力値を受け取る
        form_data["name"] = request.form.get("name", "").strip()
        form_data["login_id"] = request.form.get("login_id", "").strip()
        password = request.form.get("password", "")
        form_data["role"] = request.form.get("role")
        form_data["theme_color"] = request.form.get("theme_color")

        # 必須項目の未入力チェック
        if (
            not form_data["name"]
            or not form_data["login_id"]
            or not password
            or not form_data["role"]
            or not form_data["theme_color"]
        ):
            flash("すべての項目を入力してください。", "danger")
        # 役割は想定した値だけ許可する
        elif form_data["role"] not in {"user", "admin"}:
            flash("役割はメンバーまたは管理者を選択してください。", "danger")
        # 画面の選択肢にない色コードは受け付けない
        elif form_data["theme_color"] not in THEME_COLOR_MAP:
            flash("テーマカラーの選択が不正です。", "danger")
        elif User.query.filter_by(login_id=form_data["login_id"]).first() is not None:
            flash("そのIDはすでに使用されています。", "danger")
        else:
            # パスワードはそのまま保存せず、ハッシュ化して保存する
            new_user = User(
                login_id=form_data["login_id"],
                password_hash=generate_password_hash(password),
                name=form_data["name"],
                role=form_data["role"],
                color=THEME_COLOR_MAP[form_data["theme_color"]],
            )
            db.session.add(new_user)
            db.session.commit()
            flash("新しいシェアメンバーを登録しました。", "success")
            return redirect(url_for("admin_users"))

    # 一覧は登録順で表示する
    users = User.query.order_by(User.created_at.asc(), User.id.asc()).all()
    user_rows = [
        {
            "id": user.id,
            "login_id": user.login_id,
            "name": user.name,
            "role": user.role,
            "color": user.color,
            "created_at_display": (
                format_date_for_jst_display(user.created_at)
                if user.created_at is not None
                else ""
            ),
        }
        for user in users
    ]
    return render_template("admin_users.html", users=user_rows, form_data=form_data)


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required
@admin_required
def admin_user_delete(user_id):
    """管理者がユーザーを削除する。"""
    # 指定IDのユーザーがいない場合は一覧へ戻す
    target_user = db.session.get(User, user_id)
    if target_user is None:
        flash("削除対象のシェアメンバーが見つかりません。", "danger")
        return redirect(url_for("admin_users"))

    # ログイン中の自分自身は削除させない
    if target_user.id == current_user.id:
        flash("ログイン中のユーザー自身は削除できません。", "danger")
        return redirect(url_for("admin_users"))

    # 機器を持つユーザーは削除させない
    has_owned_device = (
        Device.query
        .filter(Device.user_id == target_user.id)
        .first()
        is not None
    )
    if has_owned_device:
        flash("登録済み機器があるメンバーは削除できません。", "danger")
        return redirect(url_for("admin_users"))

    # すでに確定請求の内訳に使われたユーザーは削除させない
    has_finalized_bill_member = (
        FinalizedBillMember.query
        .filter(FinalizedBillMember.user_id == target_user.id)
        .first()
        is not None
    )
    if has_finalized_bill_member:
        flash("確定済み電気料金の内訳データがあるメンバーは削除できません。", "danger")
        return redirect(url_for("admin_users"))

    # 運転中の記録があるユーザーは削除させない
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
        flash("運転中の機器があるメンバーは削除できません。", "danger")
        return redirect(url_for("admin_users"))

    db.session.delete(target_user)
    db.session.commit()
    flash("シェアメンバーを削除しました。", "success")

    return redirect(url_for("admin_users"))


# =============================
# ■ 管理者：機器管理
# =============================
@app.route("/admin/devices", methods=["GET", "POST"])
@login_required
@admin_required
def admin_devices():
    """管理者用の機器一覧表示と新規登録を行う。"""
    # 新規登録フォームの初期表示値
    form_data = {
        "name": "",
        "user_id": "",
        "power_kw": "",
        "theme_color": "c1",
    }

    # 機器の利用者として選べるのは一般ユーザーのみ
    users = (
        User.query
        .filter(User.role == "user")
        .order_by(User.created_at.asc(), User.id.asc())
        .all()
    )

    if request.method == "POST":
        # フォーム入力値を取得（エラー時の再表示にも利用）
        form_data["name"] = request.form.get("name", "").strip()
        form_data["user_id"] = request.form.get("user_id")
        form_data["power_kw"] = request.form.get("power_kw", "").strip()
        form_data["theme_color"] = request.form.get("theme_color")

        # 必須入力の未入力チェック
        if (
            not form_data["name"]
            or not form_data["user_id"]
            or not form_data["power_kw"]
            or not form_data["theme_color"]
        ):
            flash("すべての項目を入力してください。", "danger")
        # 画面の選択肢にない色コードは受け付けない
        elif form_data["theme_color"] not in DEVICE_THEME_COLOR_MAP:
            flash("テーマカラーの選択が不正です。", "danger")
        else:
            # user_id を数値化し、存在チェックに使う
            try:
                user_id = int(form_data["user_id"])
            except ValueError:
                user_id = None

            target_user = db.session.get(User, user_id) if user_id is not None else None
            # 存在する一般ユーザーだけを利用者として許可する
            if target_user is None or target_user.role != "user":
                flash("使用メンバーの選択が不正です。", "danger")
            else:
                # 消費電力は正の数値のみ許可する
                try:
                    power_kw = Decimal(form_data["power_kw"])
                except InvalidOperation:
                    power_kw = None

                if power_kw is None or power_kw <= 0:
                    flash("消費電力は0より大きい数値で入力してください。", "danger")
                else:
                    # 画面の選択値を実際のカラーコードに変換して保存する
                    new_device = Device(
                        name=form_data["name"],
                        user_id=target_user.id,
                        power_kw=power_kw,
                        color=DEVICE_THEME_COLOR_MAP[form_data["theme_color"]],
                    )
                    db.session.add(new_device)
                    db.session.commit()
                    flash("新しい機器を登録しました。", "success")
                    return redirect(url_for("admin_devices"))

    # 一覧表示で利用者名も使うため、関連ユーザーを同時に読み込む
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
    # 指定IDの機器がない場合は一覧へ戻す
    target_device = db.session.get(Device, device_id)
    if target_device is None:
        flash("削除対象の機器が見つかりません。", "danger")
        return redirect(url_for("admin_devices"))

    # 1件でも使用記録がある機器は削除させない
    has_usage_log = (
        DeviceUsageLog.query
        .filter(DeviceUsageLog.device_id == target_device.id)
        .first()
        is not None
    )
    if has_usage_log:
        flash("使用記録がある機器は削除できません。", "danger")
        return redirect(url_for("admin_devices"))

    db.session.delete(target_device)
    db.session.commit()
    flash("機器を削除しました。", "success")

    return redirect(url_for("admin_devices"))


# =============================
# ■ 管理者：電気料金確定
# =============================
@app.route("/admin/bills/confirm", methods=["GET", "POST"])
@login_required
@admin_required
def admin_bill_confirm():
    """電気料金確定画面を表示し、入力値の検証と保存を行う。"""
    base_context = get_admin_bill_confirm_base_context()
    is_initial_confirm = base_context["is_initial_confirm"]
    fixed_period_start_utc = base_context["fixed_period_start_utc"]
    user_members = base_context["user_members"]

    form_period_start = ""
    form_period_end = ""
    form_billing_amount = ""
    form_base_fee = ""
    form_usage_kwh = ""

    preview_result = calculate_bill_confirm_preview(
        is_initial_confirm=is_initial_confirm,
        fixed_period_start_utc=fixed_period_start_utc,
        user_members=user_members,
        form_period_start=form_period_start,
        form_period_end=form_period_end,
        form_billing_amount=form_billing_amount,
        form_base_fee=form_base_fee,
        form_usage_kwh=form_usage_kwh,
    )

    if request.method == "POST":
        # POST後にエラーが出ても再入力しやすいよう、入力値を保持する
        form_period_start = request.form.get("period_start", "").strip()
        form_period_end = request.form.get("period_end", "").strip()
        form_billing_amount = request.form.get("billing_amount", "").strip()
        form_base_fee = request.form.get("base_fee", "").strip()
        form_usage_kwh = request.form.get("usage_kwh", "").strip()
        is_confirmed_by_modal = request.form.get("is_confirmed_by_modal", "").strip().lower() == "true"

        preview_result = calculate_bill_confirm_preview(
            is_initial_confirm=is_initial_confirm,
            fixed_period_start_utc=fixed_period_start_utc,
            user_members=user_members,
            form_period_start=form_period_start,
            form_period_end=form_period_end,
            form_billing_amount=form_billing_amount,
            form_base_fee=form_base_fee,
            form_usage_kwh=form_usage_kwh,
        )

        # モーダル未経由のPOSTは保存禁止（Enterキー送信などを含む）
        if not is_confirmed_by_modal:
            if preview_result["errors"]:
                flash(preview_result["errors"][0], "danger")
            else:
                flash("確認モーダルから確定を実行してください。", "danger")
        elif preview_result["is_ready"] and preview_result["save_payload"] is not None:
            save_payload = preview_result["save_payload"]
            try:
                finalized_bill = FinalizedBill(
                    period_start=save_payload["period_start_utc"],
                    period_end=save_payload["period_end_utc"],
                    billing_amount=save_payload["billing_amount"],
                    base_fee=save_payload["base_fee"],
                    usage_kwh=save_payload["usage_kwh"],
                    unit_price=save_payload["unit_price"],
                )
                db.session.add(finalized_bill)
                db.session.flush()

                for member_row in save_payload["member_rows"]:
                    db.session.add(
                        FinalizedBillMember(
                            finalized_bill_id=finalized_bill.id,
                            user_id=member_row["user_id"],
                            device_usage_amount=member_row["device_usage_amount"],
                            equal_share_amount=member_row["equal_share_amount"],
                            share_amount=member_row["share_amount"],
                        )
                    )

                db.session.commit()
            except Exception:
                app.logger.exception("admin_bill_confirm: 確定保存時に例外が発生しました")
                db.session.rollback()
                flash("電気料金の確定に失敗しました。もう一度お試しください。", "danger")
            else:
                period_start_display = format_date_for_jst_display(save_payload["period_start_utc"])
                period_end_display = format_date_for_jst_display(save_payload["period_end_utc"])
                flash(
                    f"{period_start_display}～{period_end_display}利用分の電気料金が確定しました",
                    "success",
                )
                return redirect(url_for("admin_bills"))
        elif preview_result["errors"]:
            flash(preview_result["errors"][0], "danger")

    return render_template(
        "admin_bill_confirm.html",
        latest_created_display=base_context["latest_created_display"],
        latest_period_display=base_context["latest_period_display"],
        unfinalized_start_display=base_context["unfinalized_start_display"],
        unfinalized_notice_message=base_context["unfinalized_notice_message"],
        is_initial_confirm=is_initial_confirm,
        fixed_period_start_input=base_context["fixed_period_start_input"],
        preview_members=preview_result["preview_members"],
        unit_price_display=preview_result["unit_price_display"],
        form_period_start=form_period_start,
        form_period_end=form_period_end,
        form_billing_amount=form_billing_amount,
        form_base_fee=form_base_fee,
        form_usage_kwh=form_usage_kwh,
        modal_period_display=preview_result["modal_period_display"],
        modal_billing_amount_display=preview_result["modal_billing_amount_display"],
        modal_base_fee_display=preview_result["modal_base_fee_display"],
        modal_usage_kwh_display=preview_result["modal_usage_kwh_display"],
        modal_unit_price_display=preview_result["modal_unit_price_display"],
        confirm_preview_api_url=url_for("admin_bill_confirm_preview"),
        preview_ready=preview_result["is_ready"],
    )


@app.route("/admin/bills/confirm/preview", methods=["POST"])
@login_required
@admin_required
def admin_bill_confirm_preview():
    """入力値からプレビュー表示データのみを返す（保存はしない）。"""
    base_context = get_admin_bill_confirm_base_context()
    preview_result = calculate_bill_confirm_preview(
        is_initial_confirm=base_context["is_initial_confirm"],
        fixed_period_start_utc=base_context["fixed_period_start_utc"],
        user_members=base_context["user_members"],
        form_period_start=request.form.get("period_start", "").strip(),
        form_period_end=request.form.get("period_end", "").strip(),
        form_billing_amount=request.form.get("billing_amount", "").strip(),
        form_base_fee=request.form.get("base_fee", "").strip(),
        form_usage_kwh=request.form.get("usage_kwh", "").strip(),
    )
    return jsonify(
        {
            "ok": preview_result["is_ready"],
            "errors": preview_result["errors"],
            "unit_price_display": preview_result["unit_price_display"],
            "preview_members": preview_result["preview_members"],
            "modal_period_display": preview_result["modal_period_display"],
            "modal_billing_amount_display": preview_result["modal_billing_amount_display"],
            "modal_base_fee_display": preview_result["modal_base_fee_display"],
            "modal_usage_kwh_display": preview_result["modal_usage_kwh_display"],
            "modal_unit_price_display": preview_result["modal_unit_price_display"],
        }
    )


# =============================
# ■ 管理者：確定済み電気料金一覧
# =============================
@app.route("/admin/bills", methods=["GET"])
@login_required
@admin_required
def admin_bills():
    """管理者用の確定済み電気料金一覧画面を表示する。"""
    user_members = (
        User.query
        .filter(User.role == "user")
        .order_by(User.created_at.asc(), User.id.asc())
        .all()
    )

    # データがないときに表示する空カード
    def build_uncalculated_member_cards():
        return [
            {
                "member_name": member.name,
                "member_color": "#f2f2f2",
                "share_amount_display": "- - - - -円",
                "device_usage_amount_display": "- - - - -円",
                "equal_share_amount_display": "- - - - -円",
            }
            for member in user_members
        ]

    # 確定済み1件分の内訳を、画面用カードに整形する
    def build_member_cards_for_bill(finalized_bill):
        if (
            finalized_bill is None
            or not finalized_bill.finalized_bill_members
            or any(member.user is None for member in finalized_bill.finalized_bill_members)
        ):
            return build_uncalculated_member_cards()

        sorted_members = sorted(
            finalized_bill.finalized_bill_members,
            key=lambda member: (
                -Decimal(str(member.share_amount)),
                member.user_id,
            ),
        )
        return [
            {
                "member_name": member.user.name,
                "member_color": member.user.color,
                "share_amount_display": format_yen_for_display(member.share_amount),
                "device_usage_amount_display": format_yen_for_display(member.device_usage_amount),
                "equal_share_amount_display": format_yen_for_display(member.equal_share_amount),
            }
            for member in sorted_members
        ]

    finalized_bills = (
        FinalizedBill.query
        .options(joinedload(FinalizedBill.finalized_bill_members).joinedload(FinalizedBillMember.user))
        .order_by(FinalizedBill.period_end.desc(), FinalizedBill.id.desc())
        .all()
    )

    latest_bill = finalized_bills[0] if finalized_bills else None
    if latest_bill is not None:
        latest_date_summary = (
            f"確定日 {format_date_for_jst_display(latest_bill.created_at)}<br>"
            f"{format_date_for_jst_display(latest_bill.period_start)}～"
            f"{format_date_for_jst_display(latest_bill.period_end)}利用分"
        )
        latest_total_amount_display = f"請求総額　¥ {int(Decimal(str(latest_bill.billing_amount))):,}"
        latest_unit_price_display = (
            f"/ 単価　{format_decimal_for_display(Decimal(str(latest_bill.unit_price)))}円/kWh"
        )
        member_cards = build_member_cards_for_bill(latest_bill)
    else:
        latest_date_summary = "確定日 - - - - / - - / - -<br>- - - - / - - / - -～- - - - / - - / - -利用分"
        latest_total_amount_display = "請求総額　¥ - - -"
        latest_unit_price_display = "/ 単価　- - 円/kWh"
        member_cards = build_uncalculated_member_cards()

    history_rows = [
        {
            "bill_id": bill.id,
            "period_display": (
                f"{format_date_for_jst_display(bill.period_start)}～"
                f"{format_date_for_jst_display(bill.period_end)}"
            ),
            "created_date_display": format_date_for_jst_display(bill.created_at),
            "unit_price_display": f"{format_decimal_for_display(Decimal(str(bill.unit_price)))}円/kWh",
            "billing_amount_display": f"¥ {int(Decimal(str(bill.billing_amount))):,}",
        }
        for bill in finalized_bills
    ]

    bill_details_by_id = {
        str(bill.id): {
            "date_summary_html": (
                f"確定日 {format_date_for_jst_display(bill.created_at)}<br>"
                f"{format_date_for_jst_display(bill.period_start)}～"
                f"{format_date_for_jst_display(bill.period_end)}利用分"
            ),
            "total_amount_display": f"請求総額　¥ {int(Decimal(str(bill.billing_amount))):,}",
            "unit_price_display": f"/ 単価　{format_decimal_for_display(Decimal(str(bill.unit_price)))}円/kWh",
            "member_cards": build_member_cards_for_bill(bill),
        }
        for bill in finalized_bills
    }
    default_bill_detail = {
        "date_summary_html": "確定日 - - - - / - - / - -<br>- - - - / - - / - -～- - - - / - - / - -利用分",
        "total_amount_display": "請求総額　¥ - - -",
        "unit_price_display": "/ 単価　- - 円/kWh",
        "member_cards": build_uncalculated_member_cards(),
    }

    return render_template(
        "admin_bills.html",
        latest_date_summary=latest_date_summary,
        latest_total_amount_display=latest_total_amount_display,
        latest_unit_price_display=latest_unit_price_display,
        member_cards=member_cards,
        history_rows=history_rows,
        bill_details_by_id=bill_details_by_id,
        default_bill_detail=default_bill_detail,
    )


# =============================
# ■ 共通：レスポンス後処理
# =============================
@app.after_request
def add_no_cache_headers(response):
    """ログイン済みレスポンスにキャッシュ抑止ヘッダーを付与する。"""
    if current_user.is_authenticated:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# =============================
# ■ 共通：開発用サーバー起動設定
# =============================
if __name__ == '__main__':
    # host='0.0.0.0' にすることで、同一Wi-Fi内のスマホなど別デバイスからアクセス可能になる
    # ※本設定は開発環境用。Renderデプロイ時はGunicornで起動されるため影響なし
    app.run(host='0.0.0.0', port=5000, debug=True)

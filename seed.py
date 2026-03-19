"""
開発用シードスクリプト

実行手順:
- uv run python seed.py
- 実行すると開発用データを全削除して再投入します
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Tuple

from werkzeug.security import generate_password_hash

from app import app
from models import db, Device, DeviceUsageLog, FinalizedBill, FinalizedBillMember, User


# +09:00（日本時間）を明示的に利用する
JST = timezone(timedelta(hours=9))


def jst_datetime(
    year: int,
    month: int,
    day: int,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
) -> datetime:
    """JST(+09:00)の aware datetime を作る。"""
    return datetime(year, month, day, hour, minute, second, tzinfo=JST)


def parse_jst_date(d: str) -> date:
    """YYYY/MM/DD 文字列を date に変換する。"""
    return datetime.strptime(d, "%Y/%m/%d").date()


def parse_jst_time(t: str) -> time:
    """HH:MM 文字列を time に変換する。"""
    return datetime.strptime(t, "%H:%M").time()


def create_password_hash(user: User, raw_password: str) -> str:
    """
    既存モデルに set_password 相当があればそれを優先して使う。
    無い場合は既存アプリに合わせて generate_password_hash を使う。
    """
    set_password_method = getattr(user, "set_password", None)
    if callable(set_password_method):
        set_password_method(raw_password)
        return user.password_hash
    return generate_password_hash(raw_password)


def clear_all_data() -> Dict[str, int]:
    """
    FK制約を考慮して、子テーブルから親テーブルへ削除する。
    開発用のため、論理削除データも含めて全削除する。
    """
    deleted_counts = {}

    deleted_counts["finalized_bill_members"] = FinalizedBillMember.query.delete(
        synchronize_session=False
    )
    deleted_counts["finalized_bills"] = FinalizedBill.query.delete(
        synchronize_session=False
    )
    deleted_counts["device_usage_logs"] = DeviceUsageLog.query.delete(
        synchronize_session=False
    )
    deleted_counts["devices"] = Device.query.delete(synchronize_session=False)
    deleted_counts["users"] = User.query.delete(synchronize_session=False)

    db.session.flush()
    return deleted_counts


def seed_users() -> Dict[str, User]:
    """固定ユーザーを作成し、login_id -> User の辞書を返す。"""
    user_defs = [
        {
            "login_id": "admin",
            "password": "admin123",
            "name": "管理者",
            "role": "admin",
            "color": "#f2f2f2",
            "created_at": jst_datetime(2025, 5, 19, 9, 0, 0),
        },
        {
            "login_id": "hanako",
            "password": "test123",
            "name": "花子",
            "role": "user",
            "color": "#fff0f3",
            "created_at": jst_datetime(2025, 5, 20, 9, 0, 0),
        },
        {
            "login_id": "taro",
            "password": "test123",
            "name": "太郎",
            "role": "user",
            "color": "#f0f4ff",
            "created_at": jst_datetime(2025, 5, 20, 9, 5, 0),
        },
        {
            "login_id": "ichiro",
            "password": "test123",
            "name": "一郎",
            "role": "user",
            "color": "#f1fcf0",
            "created_at": jst_datetime(2025, 5, 20, 9, 10, 0),
        },
        {
            "login_id": "ryoko",
            "password": "test123",
            "name": "良子",
            "role": "user",
            "color": "#fff9f0",
            "created_at": jst_datetime(2025, 5, 20, 9, 15, 0),
        },
    ]

    users: List[User] = []
    user_map: Dict[str, User] = {}

    for ud in user_defs:
        user = User(
            login_id=ud["login_id"],
            password_hash="",
            name=ud["name"],
            role=ud["role"],
            color=ud["color"],
            created_at=ud["created_at"],
        )
        user.password_hash = create_password_hash(user, ud["password"])
        users.append(user)
        user_map[user.login_id] = user

    db.session.add_all(users)
    db.session.flush()
    return user_map


def seed_devices(user_map: Dict[str, User]) -> Dict[Tuple[str, str], Device]:
    """
    一般ユーザー4名に対して、各5台ずつ機器を作成する。
    後続の利用ログ紐付けで使うため (login_id, device_name) をキーに保持する。
    """
    device_defs = [
        ("暖房", Decimal("0.5"), "#ff9999"),
        ("ストーブ弱", Decimal("0.6"), "#ffcc99"),
        ("ストーブ強", Decimal("1.1"), "#ff99cc"),
        ("冷房", Decimal("0.4"), "#87ceeb"),
        ("こたつ", Decimal("0.3"), "#b380ff"),
    ]

    user_login_ids = ["hanako", "taro", "ichiro", "ryoko"]
    devices: List[Device] = []
    device_map: Dict[Tuple[str, str], Device] = {}

    for login_id in user_login_ids:
        owner = user_map[login_id]
        for name, power_kw, color in device_defs:
            device = Device(
                name=name,
                user_id=owner.id,
                power_kw=power_kw,
                color=color,
            )
            devices.append(device)
            device_map[(login_id, name)] = device

    db.session.add_all(devices)
    db.session.flush()
    return device_map


def seed_finalized_bills() -> Dict[Tuple[datetime, datetime], FinalizedBill]:
    """固定の確定済み請求8件を作成し、期間キーで参照できるようにする。"""
    bill_defs = [
        {
            "period_start": jst_datetime(2025, 12, 22, 0, 0, 0),
            "period_end": jst_datetime(2026, 1, 19, 23, 59, 59),
            "billing_amount": Decimal("24000.00"),
            "base_fee": Decimal("6798.00"),
            "usage_kwh": Decimal("610.0"),
            "unit_price": Decimal("28.20"),
            "created_at": jst_datetime(2026, 2, 21, 12, 0, 0),
        },
        {
            "period_start": jst_datetime(2025, 11, 20, 0, 0, 0),
            "period_end": jst_datetime(2025, 12, 21, 23, 59, 59),
            "billing_amount": Decimal("18000.00"),
            "base_fee": Decimal("5400.00"),
            "usage_kwh": Decimal("450.0"),
            "unit_price": Decimal("28.00"),
            "created_at": jst_datetime(2026, 1, 23, 12, 0, 0),
        },
        {
            "period_start": jst_datetime(2025, 10, 22, 0, 0, 0),
            "period_end": jst_datetime(2025, 11, 19, 23, 59, 59),
            "billing_amount": Decimal("14000.00"),
            "base_fee": Decimal("4509.50"),
            "usage_kwh": Decimal("333.0"),
            "unit_price": Decimal("28.50"),
            "created_at": jst_datetime(2025, 12, 21, 12, 0, 0),
        },
        {
            "period_start": jst_datetime(2025, 9, 20, 0, 0, 0),
            "period_end": jst_datetime(2025, 10, 21, 23, 59, 59),
            "billing_amount": Decimal("13000.00"),
            "base_fee": Decimal("4240.00"),
            "usage_kwh": Decimal("300.0"),
            "unit_price": Decimal("29.20"),
            "created_at": jst_datetime(2025, 11, 23, 12, 0, 0),
        },
        {
            "period_start": jst_datetime(2025, 8, 21, 0, 0, 0),
            "period_end": jst_datetime(2025, 9, 19, 23, 59, 59),
            "billing_amount": Decimal("19000.00"),
            "base_fee": Decimal("5176.00"),
            "usage_kwh": Decimal("480.0"),
            "unit_price": Decimal("28.80"),
            "created_at": jst_datetime(2025, 10, 30, 12, 0, 0),
        },
        {
            "period_start": jst_datetime(2025, 7, 22, 0, 0, 0),
            "period_end": jst_datetime(2025, 8, 20, 23, 59, 59),
            "billing_amount": Decimal("21000.00"),
            "base_fee": Decimal("5997.60"),
            "usage_kwh": Decimal("532.0"),
            "unit_price": Decimal("28.20"),
            "created_at": jst_datetime(2025, 9, 21, 12, 0, 0),
        },
        {
            "period_start": jst_datetime(2025, 6, 20, 0, 0, 0),
            "period_end": jst_datetime(2025, 7, 21, 23, 59, 59),
            "billing_amount": Decimal("15000.00"),
            "base_fee": Decimal("4808.00"),
            "usage_kwh": Decimal("364.0"),
            "unit_price": Decimal("28.00"),
            "created_at": jst_datetime(2025, 8, 23, 12, 0, 0),
        },
        {
            "period_start": jst_datetime(2025, 5, 21, 0, 0, 0),
            "period_end": jst_datetime(2025, 6, 19, 23, 59, 59),
            "billing_amount": Decimal("12000.00"),
            "base_fee": Decimal("3997.50"),
            "usage_kwh": Decimal("291.0"),
            "unit_price": Decimal("27.50"),
            "created_at": jst_datetime(2025, 7, 20, 12, 0, 0),
        },
    ]

    bills = [FinalizedBill(**bd) for bd in bill_defs]
    db.session.add_all(bills)
    db.session.flush()

    bill_map: Dict[Tuple[datetime, datetime], FinalizedBill] = {}
    for bill in bills:
        bill_map[(bill.period_start, bill.period_end)] = bill
    return bill_map


def seed_finalized_bill_members(
    user_map: Dict[str, User],
    bill_map: Dict[Tuple[datetime, datetime], FinalizedBill],
) -> int:
    """固定のメンバー別内訳（8請求 x 4人 = 32件）を作成する。"""
    member_defs = [
        (
            jst_datetime(2025, 12, 22, 0, 0, 0),
            jst_datetime(2026, 1, 19, 23, 59, 59),
            [
                ("hanako", "3800.00", "3000.00", "6800.00"),
                ("taro", "3200.00", "3000.00", "6200.00"),
                ("ichiro", "2600.00", "3000.00", "5600.00"),
                ("ryoko", "2400.00", "3000.00", "5400.00"),
            ],
        ),
        (
            jst_datetime(2025, 11, 20, 0, 0, 0),
            jst_datetime(2025, 12, 21, 23, 59, 59),
            [
                ("hanako", "2800.00", "2200.00", "5000.00"),
                ("taro", "2500.00", "2200.00", "4700.00"),
                ("ichiro", "2100.00", "2200.00", "4300.00"),
                ("ryoko", "1800.00", "2200.00", "4000.00"),
            ],
        ),
        (
            jst_datetime(2025, 10, 22, 0, 0, 0),
            jst_datetime(2025, 11, 19, 23, 59, 59),
            [
                ("hanako", "2200.00", "1700.00", "3900.00"),
                ("taro", "1900.00", "1700.00", "3600.00"),
                ("ichiro", "1700.00", "1700.00", "3400.00"),
                ("ryoko", "1400.00", "1700.00", "3100.00"),
            ],
        ),
        (
            jst_datetime(2025, 9, 20, 0, 0, 0),
            jst_datetime(2025, 10, 21, 23, 59, 59),
            [
                ("hanako", "2000.00", "1600.00", "3600.00"),
                ("taro", "1800.00", "1600.00", "3400.00"),
                ("ichiro", "1500.00", "1600.00", "3100.00"),
                ("ryoko", "1300.00", "1600.00", "2900.00"),
            ],
        ),
        (
            jst_datetime(2025, 8, 21, 0, 0, 0),
            jst_datetime(2025, 9, 19, 23, 59, 59),
            [
                ("hanako", "2900.00", "2300.00", "5200.00"),
                ("taro", "2700.00", "2300.00", "5000.00"),
                ("ichiro", "2200.00", "2300.00", "4500.00"),
                ("ryoko", "2000.00", "2300.00", "4300.00"),
            ],
        ),
        (
            jst_datetime(2025, 7, 22, 0, 0, 0),
            jst_datetime(2025, 8, 20, 23, 59, 59),
            [
                ("hanako", "3300.00", "2600.00", "5900.00"),
                ("taro", "2900.00", "2600.00", "5500.00"),
                ("ichiro", "2400.00", "2600.00", "5000.00"),
                ("ryoko", "2000.00", "2600.00", "4600.00"),
            ],
        ),
        (
            jst_datetime(2025, 6, 20, 0, 0, 0),
            jst_datetime(2025, 7, 21, 23, 59, 59),
            [
                ("hanako", "2400.00", "1800.00", "4200.00"),
                ("taro", "2100.00", "1800.00", "3900.00"),
                ("ichiro", "1800.00", "1800.00", "3600.00"),
                ("ryoko", "1500.00", "1800.00", "3300.00"),
            ],
        ),
        (
            jst_datetime(2025, 5, 21, 0, 0, 0),
            jst_datetime(2025, 6, 19, 23, 59, 59),
            [
                ("hanako", "2000.00", "1400.00", "3400.00"),
                ("taro", "1800.00", "1400.00", "3200.00"),
                ("ichiro", "1500.00", "1400.00", "2900.00"),
                ("ryoko", "1100.00", "1400.00", "2500.00"),
            ],
        ),
    ]

    members: List[FinalizedBillMember] = []
    for period_start, period_end, rows in member_defs:
        bill = bill_map[(period_start, period_end)]
        for login_id, device_usage_amount, equal_share_amount, share_amount in rows:
            user = user_map[login_id]
            members.append(
                FinalizedBillMember(
                    finalized_bill_id=bill.id,
                    user_id=user.id,
                    device_usage_amount=Decimal(device_usage_amount),
                    equal_share_amount=Decimal(equal_share_amount),
                    share_amount=Decimal(share_amount),
                )
            )

    db.session.add_all(members)
    db.session.flush()
    return len(members)


def build_usage_log_definitions() -> Dict[str, List[Tuple[str, str, str, str]]]:
    """固定の利用ログ定義（4人 x 30件）を返す。"""
    return {
        "hanako": [
            ("2026/01/20", "06:40", "08:00", "こたつ"),
            ("2026/01/22", "07:15", "09:05", "暖房"),
            ("2026/01/24", "08:30", "10:45", "こたつ"),
            ("2026/01/26", "18:10", "19:30", "ストーブ弱"),
            ("2026/01/28", "19:05", "20:55", "暖房"),
            ("2026/01/30", "20:25", "22:40", "こたつ"),
            ("2026/02/01", "06:40", "08:00", "ストーブ強"),
            ("2026/02/03", "07:15", "09:05", "暖房"),
            ("2026/02/05", "08:30", "10:45", "こたつ"),
            ("2026/02/07", "18:10", "19:30", "ストーブ弱"),
            ("2026/02/09", "19:05", "20:55", "こたつ"),
            ("2026/02/11", "20:25", "22:40", "暖房"),
            ("2026/02/13", "06:40", "08:00", "こたつ"),
            ("2026/02/15", "07:15", "09:05", "ストーブ弱"),
            ("2026/02/17", "08:30", "10:45", "暖房"),
            ("2026/02/19", "18:10", "19:30", "こたつ"),
            ("2026/02/21", "19:05", "20:55", "ストーブ強"),
            ("2026/02/23", "20:25", "22:40", "暖房"),
            ("2026/02/25", "06:40", "08:00", "こたつ"),
            ("2026/02/27", "07:15", "09:05", "ストーブ弱"),
            ("2026/03/01", "08:30", "10:45", "こたつ"),
            ("2026/03/03", "18:10", "19:30", "暖房"),
            ("2026/03/05", "19:05", "20:55", "こたつ"),
            ("2026/03/07", "20:25", "22:40", "ストーブ弱"),
            ("2026/03/09", "06:40", "08:00", "暖房"),
            ("2026/03/11", "07:15", "09:05", "こたつ"),
            ("2026/03/13", "08:30", "10:45", "ストーブ強"),
            ("2026/03/15", "18:10", "19:30", "暖房"),
            ("2026/03/17", "19:05", "20:55", "こたつ"),
            ("2026/03/19", "20:25", "22:40", "ストーブ弱"),
        ],
        "taro": [
            ("2026/01/21", "18:30", "19:55", "ストーブ弱"),
            ("2026/01/23", "19:25", "21:20", "こたつ"),
            ("2026/01/25", "20:20", "22:45", "暖房"),
            ("2026/01/27", "21:00", "22:25", "こたつ"),
            ("2026/01/29", "21:45", "23:40", "ストーブ強"),
            ("2026/01/31", "22:25", "00:50", "暖房"),
            ("2026/02/02", "18:30", "19:55", "こたつ"),
            ("2026/02/04", "19:25", "21:20", "ストーブ弱"),
            ("2026/02/06", "20:20", "22:45", "暖房"),
            ("2026/02/08", "21:00", "22:25", "こたつ"),
            ("2026/02/10", "21:45", "23:40", "ストーブ弱"),
            ("2026/02/12", "22:25", "00:50", "こたつ"),
            ("2026/02/14", "18:30", "19:55", "暖房"),
            ("2026/02/16", "19:25", "21:20", "こたつ"),
            ("2026/02/18", "20:20", "22:45", "ストーブ強"),
            ("2026/02/20", "21:00", "22:25", "暖房"),
            ("2026/02/22", "21:45", "23:40", "こたつ"),
            ("2026/02/24", "22:25", "00:50", "ストーブ弱"),
            ("2026/02/26", "18:30", "19:55", "暖房"),
            ("2026/02/28", "19:25", "21:20", "こたつ"),
            ("2026/03/02", "20:20", "22:45", "ストーブ弱"),
            ("2026/03/04", "21:00", "22:25", "こたつ"),
            ("2026/03/06", "21:45", "23:40", "暖房"),
            ("2026/03/08", "22:25", "00:50", "こたつ"),
            ("2026/03/10", "18:30", "19:55", "ストーブ強"),
            ("2026/03/12", "19:25", "21:20", "暖房"),
            ("2026/03/14", "20:20", "22:45", "こたつ"),
            ("2026/03/16", "21:00", "22:25", "ストーブ弱"),
            ("2026/03/18", "21:45", "23:40", "暖房"),
            ("2026/03/19", "22:25", "00:50", "こたつ"),
        ],
        "ichiro": [
            ("2026/01/20", "05:50", "07:20", "暖房"),
            ("2026/01/22", "06:35", "08:10", "ストーブ強"),
            ("2026/01/24", "07:10", "09:15", "こたつ"),
            ("2026/01/26", "21:10", "22:40", "暖房"),
            ("2026/01/28", "22:05", "23:40", "こたつ"),
            ("2026/01/30", "22:50", "00:55", "ストーブ強"),
            ("2026/02/01", "05:50", "07:20", "暖房"),
            ("2026/02/03", "06:35", "08:10", "こたつ"),
            ("2026/02/05", "07:10", "09:15", "暖房"),
            ("2026/02/07", "21:10", "22:40", "ストーブ弱"),
            ("2026/02/09", "22:05", "23:40", "暖房"),
            ("2026/02/11", "22:50", "00:55", "ストーブ強"),
            ("2026/02/13", "05:50", "07:20", "こたつ"),
            ("2026/02/15", "06:35", "08:10", "暖房"),
            ("2026/02/17", "07:10", "09:15", "こたつ"),
            ("2026/02/19", "21:10", "22:40", "ストーブ強"),
            ("2026/02/21", "22:05", "23:40", "暖房"),
            ("2026/02/23", "22:50", "00:55", "こたつ"),
            ("2026/02/25", "05:50", "07:20", "暖房"),
            ("2026/02/27", "06:35", "08:10", "ストーブ弱"),
            ("2026/03/01", "07:10", "09:15", "暖房"),
            ("2026/03/03", "21:10", "22:40", "ストーブ強"),
            ("2026/03/05", "22:05", "23:40", "こたつ"),
            ("2026/03/07", "22:50", "00:55", "暖房"),
            ("2026/03/09", "05:50", "07:20", "こたつ"),
            ("2026/03/11", "06:35", "08:10", "ストーブ強"),
            ("2026/03/13", "07:10", "09:15", "暖房"),
            ("2026/03/15", "21:10", "22:40", "こたつ"),
            ("2026/03/17", "22:05", "23:40", "暖房"),
            ("2026/03/18", "22:50", "00:55", "ストーブ弱"),
        ],
        "ryoko": [
            ("2026/01/21", "09:30", "10:45", "こたつ"),
            ("2026/01/23", "10:20", "12:05", "暖房"),
            ("2026/01/25", "11:10", "13:25", "ストーブ弱"),
            ("2026/01/27", "16:40", "17:55", "こたつ"),
            ("2026/01/29", "17:35", "19:20", "暖房"),
            ("2026/01/31", "19:20", "21:35", "こたつ"),
            ("2026/02/02", "09:30", "10:45", "ストーブ強"),
            ("2026/02/04", "10:20", "12:05", "暖房"),
            ("2026/02/06", "11:10", "13:25", "こたつ"),
            ("2026/02/08", "16:40", "17:55", "ストーブ弱"),
            ("2026/02/10", "17:35", "19:20", "こたつ"),
            ("2026/02/12", "19:20", "21:35", "暖房"),
            ("2026/02/14", "09:30", "10:45", "ストーブ弱"),
            ("2026/02/16", "10:20", "12:05", "こたつ"),
            ("2026/02/18", "11:10", "13:25", "暖房"),
            ("2026/02/20", "16:40", "17:55", "こたつ"),
            ("2026/02/22", "17:35", "19:20", "ストーブ強"),
            ("2026/02/24", "19:20", "21:35", "暖房"),
            ("2026/02/26", "09:30", "10:45", "こたつ"),
            ("2026/02/28", "10:20", "12:05", "ストーブ弱"),
            ("2026/03/02", "11:10", "13:25", "こたつ"),
            ("2026/03/04", "16:40", "17:55", "暖房"),
            ("2026/03/06", "17:35", "19:20", "ストーブ弱"),
            ("2026/03/08", "19:20", "21:35", "こたつ"),
            ("2026/03/10", "09:30", "10:45", "暖房"),
            ("2026/03/12", "10:20", "12:05", "こたつ"),
            ("2026/03/14", "11:10", "13:25", "ストーブ強"),
            ("2026/03/16", "16:40", "17:55", "暖房"),
            ("2026/03/17", "17:35", "19:20", "こたつ"),
            ("2026/03/18", "19:20", "21:35", "ストーブ弱"),
        ],
    }


def seed_device_usage_logs(device_map: Dict[Tuple[str, str], Device]) -> int:
    """
    固定の未確定利用ログを作成する。
    - 4人 x 30件 = 120件
    - すべて停止済み(end_timeあり)
    - deleted_at は全件 NULL
    - created_at=start_time, updated_at=end_time
    """
    usage_defs = build_usage_log_definitions()
    logs: List[DeviceUsageLog] = []

    for login_id, rows in usage_defs.items():
        for d, start_hm, end_hm, device_name in rows:
            day = parse_jst_date(d)
            start_time = datetime.combine(day, parse_jst_time(start_hm), tzinfo=JST)
            end_time = datetime.combine(day, parse_jst_time(end_hm), tzinfo=JST)

            # 日付またぎ（例: 22:25 -> 00:50）の場合は翌日に補正
            if end_time <= start_time:
                end_time += timedelta(days=1)

            device = device_map[(login_id, device_name)]
            logs.append(
                DeviceUsageLog(
                    start_time=start_time,
                    end_time=end_time,
                    device_id=device.id,
                    created_at=start_time,
                    updated_at=end_time,
                    deleted_at=None,
                )
            )

    db.session.add_all(logs)
    db.session.flush()
    return len(logs)


def run_seed() -> None:
    """開発用データの全削除 -> 再投入を実行する。"""
    print("=== Development seed started ===")
    deleted_counts = clear_all_data()
    print(
        "Deleted: "
        f"finalized_bill_members={deleted_counts['finalized_bill_members']}, "
        f"finalized_bills={deleted_counts['finalized_bills']}, "
        f"device_usage_logs={deleted_counts['device_usage_logs']}, "
        f"devices={deleted_counts['devices']}, "
        f"users={deleted_counts['users']}"
    )

    user_map = seed_users()
    device_map = seed_devices(user_map)
    bill_map = seed_finalized_bills()
    finalized_member_count = seed_finalized_bill_members(user_map, bill_map)
    usage_log_count = seed_device_usage_logs(device_map)

    db.session.commit()

    print("Inserted:")
    print(f"- users: {len(user_map)}")
    print(f"- devices: {len(device_map)}")
    print(f"- finalized_bills: {len(bill_map)}")
    print(f"- finalized_bill_members: {finalized_member_count}")
    print(f"- device_usage_logs: {usage_log_count}")
    print("=== Development seed completed successfully ===")


if __name__ == "__main__":
    # Flaskの設定・DBセッションへ安全にアクセスするため app context を使う
    with app.app_context():
        try:
            run_seed()
        except Exception as exc:
            db.session.rollback()
            print(f"Seed failed. Rolled back changes. Error: {exc}")
            raise

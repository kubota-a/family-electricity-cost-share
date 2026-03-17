from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from sqlalchemy.sql import func


db = SQLAlchemy()


# ---DBモデル---


class User(UserMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (    # CHECK制約
        db.CheckConstraint("role IN ('user', 'admin')", name="ck_users_role"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    login_id = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(20), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    color = db.Column(db.String(20), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    devices = db.relationship("Device", back_populates="user")
    finalized_bill_members = db.relationship(
        "FinalizedBillMember", back_populates="user"
    )


class Device(db.Model):
    __tablename__ = "devices"

    id = db.Column(db.BigInteger, primary_key=True)
    name = db.Column(db.String(20), nullable=False)
    user_id = db.Column(
        db.BigInteger,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    power_kw = db.Column(db.Numeric(6, 3), nullable=False)
    color = db.Column(db.String(20), nullable=False)

    user = db.relationship("User", back_populates="devices")
    usage_logs = db.relationship("DeviceUsageLog", back_populates="device")


class DeviceUsageLog(db.Model):
    __tablename__ = "device_usage_logs"
    __table_args__ = (    # CHECK制約
        db.CheckConstraint(
            "end_time IS NULL OR start_time < end_time",
            name="ck_usage_log_time_order",
        ),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    start_time = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    end_time = db.Column(db.DateTime(timezone=True), nullable=True)
    device_id = db.Column(
        db.BigInteger,
        db.ForeignKey("devices.id"),
        nullable=False,
        index=True,
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    device = db.relationship("Device", back_populates="usage_logs")


class FinalizedBill(db.Model):
    __tablename__ = "finalized_bills"
    __table_args__ = (    # CHECK制約
        db.CheckConstraint(
            "period_start < period_end",
            name="ck_bill_period_order",
        ),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    period_start = db.Column(db.DateTime(timezone=True), nullable=False)
    period_end = db.Column(db.DateTime(timezone=True), nullable=False)
    billing_amount = db.Column(db.Numeric(10, 2), nullable=False)
    base_fee = db.Column(db.Numeric(10, 2), nullable=False)
    usage_kwh = db.Column(db.Numeric(6, 3), nullable=False)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    finalized_bill_members = db.relationship(
        "FinalizedBillMember", back_populates="finalized_bill"
    )


class FinalizedBillMember(db.Model):
    __tablename__ = "finalized_bill_members"
    __table_args__ = (    # UNIQUE制約
        db.UniqueConstraint("finalized_bill_id", "user_id"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    finalized_bill_id = db.Column(
        db.BigInteger,
        db.ForeignKey("finalized_bills.id"),
        nullable=False,
        index=True,
    )
    user_id = db.Column(
        db.BigInteger,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    device_usage_amount = db.Column(db.Numeric(10, 2), nullable=False)
    equal_share_amount = db.Column(db.Numeric(10, 2), nullable=False)
    share_amount = db.Column(db.Numeric(10, 2), nullable=False)

    finalized_bill = db.relationship(
        "FinalizedBill", back_populates="finalized_bill_members"
    )
    user = db.relationship("User", back_populates="finalized_bill_members")


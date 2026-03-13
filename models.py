from dataclasses import dataclass
from datetime import datetime


@dataclass
class UsageRecord:
    """シンプルなプレースホルダ用モデル（後で本実装に差し替え予定）"""

    id: int
    user_name: str
    device_name: str
    started_at: datetime
    stopped_at: datetime | None
    is_deleted: bool = False


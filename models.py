from flask_sqlalchemy import SQLAlchemy  # SQLAlchemy（ORM：PythonのクラスでDBを扱えるライブラリ）
# from flask_login import UserMixin
# from werkzeug.security import generate_password_hash, check_password_hash
# from sqlalchemy.sql import func  # Postgresでも安全な日時関数


# アプリ全体で共有する DB オブジェクト
db = SQLAlchemy()


# --- ○○モデル---
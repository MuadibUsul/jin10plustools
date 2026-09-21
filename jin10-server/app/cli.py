"""管理命令（团队与账号）：

    python -m app.cli create-team "PLUS 研究组"
    python -m app.cli create-user --team "PLUS 研究组" --email a@x.com --name 张三 --role admin
    python -m app.cli reset-password --email a@x.com
    python -m app.cli list-users

密码通过交互输入（或环境变量 PLUS_NEW_PASSWORD，便于脚本化部署），不会出现在命令行历史里。
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Role, Team, User
from app.db.session import get_session_factory
from app.security import hash_password

MIN_PASSWORD = 8


def _read_password() -> str:
    password = os.environ.get("PLUS_NEW_PASSWORD") or getpass.getpass("密码（至少 8 位）：")
    if len(password) < MIN_PASSWORD:
        sys.exit("密码至少 8 位")
    if "PLUS_NEW_PASSWORD" not in os.environ and getpass.getpass("再次输入：") != password:
        sys.exit("两次输入不一致")
    return password


def _team_by_name(db: Session, name: str) -> Team:
    team = db.scalar(select(Team).where(Team.name == name))
    if team is None:
        sys.exit(f"团队不存在：{name}")
    return team


def create_team(db: Session, args: argparse.Namespace) -> None:
    if db.scalar(select(Team).where(Team.name == args.name)):
        sys.exit(f"团队已存在：{args.name}")
    team = Team(name=args.name)
    db.add(team)
    db.commit()
    print(f"已创建团队「{team.name}」 {team.id}")


def create_user(db: Session, args: argparse.Namespace) -> None:
    team = _team_by_name(db, args.team)
    email = args.email.strip().lower()
    if db.scalar(select(User).where(func.lower(User.email) == email)):
        sys.exit(f"邮箱已被使用：{email}")
    user = User(team_id=team.id, email=email, name=args.name, role=args.role, password_hash=hash_password(_read_password()))
    db.add(user)
    db.commit()
    print(f"已创建账号 {user.email}（{user.role}）→ 团队「{team.name}」")


def reset_password(db: Session, args: argparse.Namespace) -> None:
    user = db.scalar(select(User).where(func.lower(User.email) == args.email.strip().lower()))
    if user is None:
        sys.exit(f"账号不存在：{args.email}")
    user.password_hash = hash_password(_read_password())
    db.commit()
    print(f"已重置 {user.email} 的密码")


def list_users(db: Session, _args: argparse.Namespace) -> None:
    for user in db.scalars(select(User).order_by(User.team_id, User.email)):
        state = "" if user.is_active else "（已停用）"
        print(f"{user.team.name}\t{user.email}\t{user.name}\t{user.role}{state}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-team")
    p.add_argument("name")
    p.set_defaults(func=create_team)

    p = sub.add_parser("create-user")
    p.add_argument("--team", required=True)
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--role", choices=[r.value for r in Role], default=Role.EDITOR.value)
    p.set_defaults(func=create_user)

    p = sub.add_parser("reset-password")
    p.add_argument("--email", required=True)
    p.set_defaults(func=reset_password)

    p = sub.add_parser("list-users")
    p.set_defaults(func=list_users)

    args = parser.parse_args()
    with get_session_factory()() as db:
        args.func(db, args)


if __name__ == "__main__":
    main()

"""页面外壳路由（学生端首页 / 教师端首页 / 教师端讲义管理）。

与仓库里其它页面一致：服务端只返回 200 空壳，谁可以看由两处决定 ——
  * 前端：CCAuth.guard()（无 token → /login）+ 页面脚本里对 user.role 的判定
  * 接口：真正的强制在各 JSON API 的 @require_auth / @role_required 上
所以这里不做 302、不做 403。
"""
from flask import Blueprint, render_template

pages_bp = Blueprint("pages", __name__)


@pages_bp.route("/student/home", methods=["GET"])
def student_home():
    """学生端「本班首页」"""
    return render_template("student_home.html")


@pages_bp.route("/teacher/home", methods=["GET"])
def teacher_home():
    """教师端「本班首页」"""
    return render_template("teacher_home.html")


@pages_bp.route("/teacher/materials", methods=["GET"])
def teacher_materials():
    """教师端「讲义管理」"""
    from .materials import SUBJECT_CHOICES
    return render_template("teacher_materials.html", SUBJECT_CHOICES=SUBJECT_CHOICES)


@pages_bp.route("/student/homework", methods=["GET"])
def student_homework():
    """学生端「交作业」"""
    return render_template("student_homework.html")


@pages_bp.route("/student/wrong", methods=["GET"])
def student_wrong():
    """学生端「错题」"""
    return render_template("student_wrong.html")


@pages_bp.route("/teacher/grading", methods=["GET"])
def teacher_grading():
    """教师端「作业与改分」"""
    return render_template("teacher_grading.html")
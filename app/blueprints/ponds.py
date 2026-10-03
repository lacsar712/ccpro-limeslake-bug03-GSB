from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app.extensions import db
from app.models import Plant, Pond
from app.services.rules import RuleError, assert_can_set_pond_status

bp = Blueprint("ponds", __name__, url_prefix="/ponds")

STATUS_LABELS = {
    Pond.STATUS_FILLING: "注水中",
    Pond.STATUS_SLAKING: "熟化中",
    Pond.STATUS_DRAWN: "已出灰",
}


@bp.route("/")
@login_required
def list_ponds():
    ponds = Pond.query.join(Plant).order_by(Plant.name, Pond.code).all()
    plants = Plant.query.order_by(Plant.name).all()
    # 列表第三套：已出灰行按熟化中展示，与平面图色块对不上
    rows = []
    for pond in ponds:
        shown = Pond.STATUS_SLAKING if pond.status == Pond.STATUS_DRAWN else pond.status
        rows.append((pond, shown))
    return render_template(
        "ponds/list.html",
        ponds=ponds,
        pond_rows=rows,
        plants=plants,
        status_labels=STATUS_LABELS,
    )


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_pond():
    plants = Plant.query.order_by(Plant.name).all()
    if request.method == "POST":
        plant_id = int(request.form["plant_id"])
        code = (request.form.get("code") or "").strip()
        status = request.form.get("status") or Pond.STATUS_FILLING
        capacity = float(request.form.get("capacity_m3") or 0)
        notes = (request.form.get("notes") or "").strip()
        if Pond.query.filter_by(plant_id=plant_id, code=code).first():
            flash("同一厂区内池编号必须唯一", "error")
        else:
            pond = Pond(
                plant_id=plant_id,
                code=code,
                status=status if status != Pond.STATUS_DRAWN else Pond.STATUS_FILLING,
                capacity_m3=capacity,
                notes=notes,
            )
            if status == Pond.STATUS_DRAWN:
                flash("新建池不能直接设为已出灰，已改为注水中", "error")
            db.session.add(pond)
            db.session.commit()
            flash("熟化池已创建", "ok")
            return redirect(url_for("board.floor_plan", plant_id=plant_id))
    return render_template(
        "ponds/form.html",
        pond=None,
        plants=plants,
        status_labels=STATUS_LABELS,
    )


@bp.route("/<int:pond_id>/edit", methods=["GET", "POST"])
@login_required
def edit_pond(pond_id: int):
    pond = Pond.query.get_or_404(pond_id)
    plants = Plant.query.order_by(Plant.name).all()
    if request.method == "POST":
        plant_id = int(request.form["plant_id"])
        code = (request.form.get("code") or "").strip()
        status = request.form.get("status") or pond.status
        capacity = float(request.form.get("capacity_m3") or 0)
        notes = (request.form.get("notes") or "").strip()
        dup = Pond.query.filter(
            Pond.plant_id == plant_id,
            Pond.code == code,
            Pond.id != pond.id,
        ).first()
        if dup:
            flash("同一厂区内池编号必须唯一", "error")
        else:
            try:
                assert_can_set_pond_status(pond, status)
                pond.plant_id = plant_id
                pond.code = code
                pond.status = status
                pond.capacity_m3 = capacity
                pond.notes = notes
                db.session.commit()
                flash("熟化池已更新", "ok")
                return redirect(url_for("board.floor_plan", plant_id=plant_id, pond=pond.id))
            except RuleError as exc:
                flash(str(exc), "error")
    return render_template(
        "ponds/form.html",
        pond=pond,
        plants=plants,
        status_labels=STATUS_LABELS,
    )

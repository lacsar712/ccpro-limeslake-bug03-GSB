from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app.extensions import db
from app.models import Pond, SlakeBatch
from app.services.rules import RuleError, assert_pond_accepts_peak_edit

bp = Blueprint("batches", __name__, url_prefix="/batches")


@bp.route("/")
@login_required
def list_batches():
    batches = (
        SlakeBatch.query.join(Pond)
        .order_by(SlakeBatch.started_at.desc())
        .all()
    )
    return render_template("batches/list.html", batches=batches)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_batch():
    ponds = Pond.query.order_by(Pond.code).all()
    if request.method == "POST":
        pond_id = int(request.form["pond_id"])
        started_raw = request.form.get("started_at") or ""
        target = float(request.form.get("target_temp_c") or 80)
        peak_raw = (request.form.get("peak_temp_c") or "").strip()
        notes = (request.form.get("notes") or "").strip()
        started_at = (
            datetime.fromisoformat(started_raw)
            if started_raw
            else datetime.utcnow()
        )
        pond = (
            db.session.query(Pond)
            .filter_by(id=pond_id)
            .with_for_update()
            .one_or_none()
        )
        if pond is None:
            db.session.rollback()
            flash("所选熟化池不存在", "error")
            return redirect(url_for("batches.create_batch"))
        try:
            peak = float(peak_raw) if peak_raw else None
            # 已出灰池不得再借“登记批次”写入峰值；本守卫同时覆盖并发提交
            if peak is not None:
                assert_pond_accepts_peak_edit(pond)
            batch = SlakeBatch(
                pond_id=pond_id,
                started_at=started_at,
                target_temp_c=target,
                peak_temp_c=peak,
                notes=notes,
            )
            db.session.add(batch)
            db.session.commit()
            flash("熟化批次已登记", "ok")
        except (RuleError, ValueError) as exc:
            db.session.rollback()
            if isinstance(exc, ValueError) and not isinstance(exc, RuleError):
                flash("表单数值格式无效", "error")
            else:
                flash(str(exc), "error")
            return render_template("batches/form.html", ponds=ponds, batch=None)
        return redirect(
            url_for(
                "board.floor_plan",
                plant_id=pond.plant_id,
                pond=pond_id,
            )
        )
    return render_template("batches/form.html", ponds=ponds, batch=None)


@bp.route("/<int:batch_id>/edit", methods=["GET", "POST"])
@login_required
def edit_batch(batch_id: int):
    batch = SlakeBatch.query.get_or_404(batch_id)
    ponds = Pond.query.order_by(Pond.code).all()
    if request.method == "POST":
        pond_id = int(request.form["pond_id"])
        started_raw = request.form.get("started_at") or ""
        target_raw = (request.form.get("target_temp_c") or "").strip()
        peak_submitted = "peak_temp_c" in request.form
        peak_raw = (request.form.get("peak_temp_c") or "").strip()
        notes = (request.form.get("notes") or "").strip()

        pond = (
            db.session.query(Pond)
            .filter_by(id=pond_id)
            .with_for_update()
            .one_or_none()
        )
        if pond is None:
            db.session.rollback()
            flash("所选熟化池不存在", "error")
            return render_template(
                "batches/form.html", ponds=ponds, batch=batch
            )
        try:
            changing_peak = peak_submitted and (
                peak_raw != ""
                or batch.peak_temp_c is not None
            )
            # 已出灰池：峰值一律不得修改。输入框在页面上已锁定，此处为服务端兜底
            if changing_peak:
                assert_pond_accepts_peak_edit(pond)
                batch.peak_temp_c = float(peak_raw) if peak_raw else None
            if started_raw:
                batch.started_at = datetime.fromisoformat(started_raw)
            batch.pond_id = pond_id
            batch.target_temp_c = float(target_raw or 80)
            batch.notes = notes
            db.session.commit()
            flash("熟化批次已更新", "ok")
        except (RuleError, ValueError) as exc:
            db.session.rollback()
            if isinstance(exc, ValueError) and not isinstance(exc, RuleError):
                flash("表单数值格式无效", "error")
            else:
                flash(str(exc), "error")
            return render_template(
                "batches/form.html", ponds=ponds, batch=batch
            )
        return redirect(
            url_for(
                "board.floor_plan",
                plant_id=pond.plant_id,
                pond=pond_id,
            )
        )
    return render_template("batches/form.html", ponds=ponds, batch=batch)

from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app.extensions import db
from app.models import Pond, SlakeBatch
from app.services.rules import RuleError, assert_can_record_peak

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
        try:
            peak = float(peak_raw) if peak_raw else None
        except ValueError:
            flash("峰值温度格式无效", "error")
            return render_template("batches/form.html", ponds=ponds, batch=None)
        pond = db.session.get(Pond, pond_id)
        if pond is None:
            flash("所选熟化池不存在", "error")
            return render_template("batches/form.html", ponds=ponds, batch=None)
        if peak is not None:
            try:
                # 已出灰池的峰值锁死，新建批次同样不得带入峰值。
                assert_can_record_peak(pond)
            except RuleError as exc:
                flash(str(exc), "error")
                return render_template("batches/form.html", ponds=ponds, batch=None)
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
        new_pond_id = int(request.form["pond_id"])
        target_pond = db.session.get(Pond, new_pond_id)
        if target_pond is None:
            flash("所选熟化池不存在", "error")
            return render_template("batches/form.html", ponds=ponds, batch=batch)
        started_raw = request.form.get("started_at") or ""
        if started_raw:
            batch.started_at = datetime.fromisoformat(started_raw)
        batch.target_temp_c = float(request.form.get("target_temp_c") or 80)
        peak_raw = (request.form.get("peak_temp_c") or "").strip()
        if target_pond.status == Pond.STATUS_DRAWN:
            # 终态池峰值锁死：表单已禁用该字段，服务端忽略任何提交值，保持原值。
            new_peak = batch.peak_temp_c
        else:
            try:
                new_peak = float(peak_raw) if peak_raw else None
            except ValueError:
                flash("峰值温度格式无效", "error")
                return render_template("batches/form.html", ponds=ponds, batch=batch)
        batch.pond_id = new_pond_id
        batch.peak_temp_c = new_peak
        batch.notes = (request.form.get("notes") or "").strip()
        db.session.commit()
        flash("熟化批次已更新", "ok")
        return redirect(
            url_for(
                "board.floor_plan",
                plant_id=target_pond.plant_id,
                pond=new_pond_id,
            )
        )
    return render_template("batches/form.html", ponds=ponds, batch=batch)

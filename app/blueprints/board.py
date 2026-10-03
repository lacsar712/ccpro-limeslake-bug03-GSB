from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app.extensions import db
from app.models import Plant, Pond
from app.services.rules import (
    RuleError,
    assert_can_set_pond_status,
    assert_pond_accepts_peak_edit,
    latest_batch_for_pond,
)

bp = Blueprint("board", __name__, url_prefix="/board")

STATUS_LABELS = {
    Pond.STATUS_FILLING: "注水中",
    Pond.STATUS_SLAKING: "熟化中",
    Pond.STATUS_DRAWN: "已出灰",
}


@bp.route("/")
@login_required
def floor_plan():
    plants = Plant.query.order_by(Plant.name).all()
    plant_id_raw = request.args.get("plant_id", "").strip()
    active_plant = None
    if plant_id_raw.isdigit():
        active_plant = db.session.get(Plant, int(plant_id_raw))
    if active_plant is None and plants:
        active_plant = plants[0]

    ponds = []
    if active_plant:
        ponds = (
            Pond.query.filter_by(plant_id=active_plant.id)
            .order_by(Pond.code)
            .all()
        )

    pond_cards = []
    for pond in ponds:
        batch = latest_batch_for_pond(pond)
        pond_cards.append({"pond": pond, "batch": batch})

    selected_id = request.args.get("pond", type=int)
    selected = None
    selected_batch = None
    if selected_id:
        selected = next((c["pond"] for c in pond_cards if c["pond"].id == selected_id), None)
        if selected:
            selected_batch = latest_batch_for_pond(selected)

    return render_template(
        "board/floor.html",
        plants=plants,
        active_plant=active_plant,
        pond_cards=pond_cards,
        selected=selected,
        selected_batch=selected_batch,
        status_labels=STATUS_LABELS,
    )


@bp.route("/ponds/<int:pond_id>/ops", methods=["POST"])
@login_required
def pond_ops(pond_id: int):
    # 先锁行再做任何校验/写入：并发的两笔操作在此串行化，
    # 后到者会读到最新的「已出灰」状态并被规则挡下。
    pond = (
        db.session.query(Pond)
        .filter_by(id=pond_id)
        .with_for_update()
        .one_or_none()
    )
    if pond is None:
        db.session.rollback()
        abort(404)

    status_raw = (request.form.get("status") or "").strip()
    # 已锁定的输入框不会提交该字段；只有显式提交峰值才视为一次写入。
    peak_submitted = "peak_temp_c" in request.form
    peak_raw = (request.form.get("peak_temp_c") or "").strip()
    notes = (request.form.get("batch_notes") or "").strip()

    batch = latest_batch_for_pond(pond)
    if batch is None:
        db.session.rollback()
        flash("该池尚无熟化批次，无法登记峰值或出灰", "error")
        return redirect(
            url_for("board.floor_plan", plant_id=pond.plant_id, pond=pond.id)
        )

    try:
        if peak_submitted:
            # 已出灰即终态：峰值字段冻结，任何提交（含并发两笔）一律挡下
            assert_pond_accepts_peak_edit(pond)
        if peak_raw:
            try:
                batch.peak_temp_c = float(peak_raw)
            except ValueError:
                raise RuleError("峰值温度格式无效")

        batch.notes = notes

        new_status = status_raw or pond.status
        assert_can_set_pond_status(pond, new_status)
        pond.status = new_status

        db.session.commit()
        flash(f"{pond.code} 已更新", "ok")
    except RuleError as exc:
        # 只回滚，绝不改状态：已出灰仍为已出灰
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(
        url_for("board.floor_plan", plant_id=pond.plant_id, pond=pond.id)
    )

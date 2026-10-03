from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app.extensions import db
from app.models import Plant, Pond
from app.services.rules import (
    RuleError,
    assert_can_record_peak,
    assert_can_set_pond_status,
    latest_batch_for_pond,
    peak_value_changed,
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
    # 行级锁串行化同一池位的并发作业提交，避免出灰与峰值写入互相覆盖。
    pond = Pond.query.with_for_update().filter_by(id=pond_id).one_or_404()
    status = request.form.get("status") or pond.status
    peak_raw = (request.form.get("peak_temp_c") or "").strip()
    notes = (request.form.get("batch_notes") or "").strip()

    def back():
        return redirect(
            url_for("board.floor_plan", plant_id=pond.plant_id, pond=pond.id)
        )

    batch = latest_batch_for_pond(pond)
    if batch is None:
        flash("该池尚无熟化批次，无法登记峰值或出灰", "error")
        return back()

    new_peak = batch.peak_temp_c
    if peak_raw:
        try:
            new_peak = float(peak_raw)
        except ValueError:
            flash("峰值温度格式无效", "error")
            return back()
    peak_changing = bool(peak_raw) and peak_value_changed(batch.peak_temp_c, new_peak)

    # 所有规则校验通过前不写任何字段；失败仅回滚重定向，池态保持库里原值。
    try:
        if peak_changing:
            # 已出灰即终态：峰值锁死，两人并发的两笔修改都会在这里被挡下。
            assert_can_record_peak(pond)
            batch.peak_temp_c = new_peak
        assert_can_set_pond_status(pond, status)
    except RuleError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return back()

    batch.notes = notes
    pond.status = status
    db.session.commit()
    flash(f"{pond.code} 已更新", "ok")
    return back()

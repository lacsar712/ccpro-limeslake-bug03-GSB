"""端到端回归与并发测试（需真 Postgres，行锁语义 SQLite 无法验证）。"""

import threading

import pgserver
import pytest

from app import create_app
from app.extensions import db
from app.models import Plant, Pond, SlakeBatch, User


@pytest.fixture(scope="session")
def pg_uri():
    server = pgserver.get_server("/tmp/limeslake-test-pg", cleanup_mode=None)
    uri = server.get_uri().replace("postgresql://", "postgresql+psycopg2://", 1)
    yield uri
    server.cleanup()


@pytest.fixture()
def app(pg_uri, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg_uri)
    application = create_app()
    application.config["WTF_CSRF_ENABLED"] = False
    with application.app_context():
        db.drop_all()
        db.create_all()
        admin = User(username="admin", role="admin")
        admin.set_password("123456")
        db.session.add(admin)
        db.session.commit()
    yield application


@pytest.fixture()
def client(app):
    c = app.test_client()
    resp = c.post(
        "/auth/login",
        data={"username": "admin", "password": "123456"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    return c


def make_pond(code, status, peak=None, target=80.0):
    plant = Plant.query.filter_by(name="测试厂").first()
    if plant is None:
        plant = Plant(name="测试厂", location="测试地")
        db.session.add(plant)
        db.session.flush()
    pond = Pond(
        plant_id=plant.id, code=code, status=status, capacity_m3=40.0
    )
    db.session.add(pond)
    db.session.flush()
    db.session.add(
        SlakeBatch(
            pond_id=pond.id,
            target_temp_c=target,
            peak_temp_c=peak,
            notes="测试批次",
        )
    )
    db.session.commit()
    return pond


def refresh(pond_id):
    db.session.expire_all()
    return db.session.get(Pond, pond_id)


# ---------- 峰值锁定：抽屉 ops 链 ----------

def test_ops_rejects_peak_write_on_drawn(app, client):
    with app.app_context():
        pond = make_pond("D1", Pond.STATUS_DRAWN, peak=91.0)
        pid, old_peak = pond.id, 91.0

    # 绕过 UI 直接提交（模拟篡改表单），服务端必须挡下
    resp = client.post(
        f"/board/ponds/{pid}/ops",
        data={"peak_temp_c": "55", "batch_notes": "x", "status": "drawn"},
        follow_redirects=True,
    )
    assert "已锁定" in resp.get_data(as_text=True)
    with app.app_context():
        p = refresh(pid)
        assert p.status == Pond.STATUS_DRAWN
        assert p.batches[0].peak_temp_c == old_peak


def test_ops_rejects_status_rollback_on_drawn(app, client):
    with app.app_context():
        pond = make_pond("D2", Pond.STATUS_DRAWN, peak=91.0)
        pid = pond.id

    resp = client.post(
        f"/board/ponds/{pid}/ops",
        data={"batch_notes": "", "status": "slaking"},
        follow_redirects=True,
    )
    assert "锁定" in resp.get_data(as_text=True)
    with app.app_context():
        assert refresh(pid).status == Pond.STATUS_DRAWN


def test_failed_peak_validation_does_not_flip_status(app, client):
    with app.app_context():
        pond = make_pond("S1", Pond.STATUS_SLAKING, peak=None)
        pid = pond.id

    resp = client.post(
        f"/board/ponds/{pid}/ops",
        data={"peak_temp_c": "abc", "batch_notes": "", "status": "slaking"},
        follow_redirects=True,
    )
    assert "格式无效" in resp.get_data(as_text=True)
    with app.app_context():
        p = refresh(pid)
        assert p.status == Pond.STATUS_SLAKING
        assert p.batches[0].peak_temp_c is None


# ---------- 并发：两人同改一个已出灰池 ----------

def test_concurrent_peak_writes_on_drawn_both_blocked(app):
    with app.app_context():
        pond = make_pond("DC", Pond.STATUS_DRAWN, peak=88.0)
        pid, old_peak = pond.id, 88.0

    results = []
    barrier = threading.Barrier(2)

    def worker(value):
        c = app.test_client()
        c.post("/auth/login", data={"username": "admin", "password": "123456"})
        barrier.wait(timeout=10)
        resp = c.post(
            f"/board/ponds/{pid}/ops",
            data={"peak_temp_c": str(value), "batch_notes": "", "status": "drawn"},
            follow_redirects=True,
        )
        results.append((value, resp.status_code, resp.get_data(as_text=True)))

    t1 = threading.Thread(target=worker, args=(11.0,))
    t2 = threading.Thread(target=worker, args=(22.0,))
    t1.start(); t2.start()
    t1.join(15); t2.join(15)
    assert not t1.is_alive() and not t2.is_alive()

    # 两笔都必须被挡下
    assert len(results) == 2
    for _, code, body in results:
        assert code == 200
        assert "已锁定" in body

    with app.app_context():
        p = refresh(pid)
        assert p.status == Pond.STATUS_DRAWN
        assert p.batches[0].peak_temp_c == old_peak


def test_concurrent_draw_vs_peak_ends_consistent(app):
    """出灰与改峰值并发：最终必须为已出灰，且不出现静默改值+拨回。"""
    with app.app_context():
        pond = make_pond("RC", Pond.STATUS_SLAKING, peak=72.0)
        pid = pond.id

    barrier = threading.Barrier(2)
    outcomes = {}

    def draw():
        c = app.test_client()
        c.post("/auth/login", data={"username": "admin", "password": "123456"})
        barrier.wait(timeout=10)
        resp = c.post(
            f"/board/ponds/{pid}/ops",
            data={"batch_notes": "", "status": "drawn"},
            follow_redirects=True,
        )
        outcomes["draw"] = resp.get_data(as_text=True)

    def peak():
        c = app.test_client()
        c.post("/auth/login", data={"username": "admin", "password": "123456"})
        barrier.wait(timeout=10)
        resp = c.post(
            f"/board/ponds/{pid}/ops",
            data={"peak_temp_c": "99", "batch_notes": "", "status": "slaking"},
            follow_redirects=True,
        )
        outcomes["peak"] = resp.get_data(as_text=True)

    t1 = threading.Thread(target=draw)
    t2 = threading.Thread(target=peak)
    t1.start(); t2.start()
    t1.join(15); t2.join(15)
    assert not t1.is_alive() and not t2.is_alive()

    with app.app_context():
        p = refresh(pid)
        assert p.status == Pond.STATUS_DRAWN  # 先出灰后改峰值必被挡；先改峰值则出灰成功
        assert p.batches[0].peak_temp_c in (72.0, 99.0)
        # 若峰值被挡，页面须有锁定提示
        if p.batches[0].peak_temp_c == 72.0:
            assert "已锁定" in outcomes["peak"]


# ---------- 峰值锁定：批次表单链 ----------

def test_edit_batch_blocks_peak_on_drawn(app, client):
    with app.app_context():
        pond = make_pond("D3", Pond.STATUS_DRAWN, peak=91.0)
        pid, bid = pond.id, pond.batches[0].id

    resp = client.post(
        f"/batches/{bid}/edit",
        data={
            "pond_id": str(pid),
            "target_temp_c": "80",
            "peak_temp_c": "123",
            "notes": "hax",
        },
        follow_redirects=True,
    )
    assert "已锁定" in resp.get_data(as_text=True)
    with app.app_context():
        p = refresh(pid)
        assert p.status == Pond.STATUS_DRAWN
        assert p.batches[0].peak_temp_c == 91.0


def test_create_batch_blocks_peak_on_drawn(app, client):
    with app.app_context():
        pond = make_pond("D4", Pond.STATUS_DRAWN, peak=91.0)
        pid = pond.id
        before = SlakeBatch.query.filter_by(pond_id=pid).count()
    resp = client.post(
        "/batches/new",
        data={
            "pond_id": str(pid),
            "target_temp_c": "80",
            "peak_temp_c": "50",
            "notes": "side channel",
        },
        follow_redirects=True,
    )
    assert "已锁定" in resp.get_data(as_text=True)
    with app.app_context():
        assert SlakeBatch.query.filter_by(pond_id=pid).count() == before
        assert refresh(pid).status == Pond.STATUS_DRAWN


# ---------- 出灰规则 ----------

def test_can_draw_when_peak_qualified(app, client):
    with app.app_context():
        pond = make_pond("S2", Pond.STATUS_SLAKING, peak=72.0)
        pid = pond.id
    resp = client.post(
        f"/board/ponds/{pid}/ops",
        data={"batch_notes": "", "status": "drawn"},
        follow_redirects=True,
    )
    assert "已更新" in resp.get_data(as_text=True)
    with app.app_context():
        assert refresh(pid).status == Pond.STATUS_DRAWN


def test_cannot_draw_when_peak_low(app, client):
    with app.app_context():
        pond = make_pond("S3", Pond.STATUS_SLAKING, peak=50.0)
        pid = pond.id
    resp = client.post(
        f"/board/ponds/{pid}/ops",
        data={"batch_notes": "", "status": "drawn"},
        follow_redirects=True,
    )
    body = resp.get_data(as_text=True)
    assert "低于" in body
    with app.app_context():
        assert refresh(pid).status == Pond.STATUS_SLAKING


# ---------- 列表与平面图同态 ----------

def test_list_and_floor_show_same_drawn_state(app, client):
    with app.app_context():
        plant = Plant(name="同态厂")
        db.session.add(plant)
        db.session.flush()
        drawn = Pond(plant_id=plant.id, code="L1", status=Pond.STATUS_DRAWN)
        slaking = Pond(plant_id=plant.id, code="L2", status=Pond.STATUS_SLAKING)
        db.session.add_all([drawn, slaking])
        db.session.commit()
        plant_id = plant.id

    lst = client.get("/ponds/").get_data(as_text=True)
    floor = client.get(f"/board/?plant_id={plant_id}").get_data(as_text=True)

    # 列表：已出灰行必须是 badge-drawn 且文案“已出灰”
    assert 'badge-drawn' in lst and 'badge-slaking' in lst
    row_drawn = lst.split("L1")[1].split("</tr>")[0]
    assert "已出灰" in row_drawn and "熟化中" not in row_drawn
    # 平面图：L1 瓦片必须带 status-drawn
    import re
    i = floor.find('tile-code">L1')
    head = floor[max(0, i - 400):i]
    m = re.search(r'class="pond-tile ([^"]*)"', head, re.S)
    assert m and "status-drawn" in m.group(1)

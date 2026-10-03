"""石灰熟化池业务规则。"""

from __future__ import annotations

from app.models import Pond, SlakeBatch

MIN_PEAK_TEMP_FOR_DRAWN = 60.0


class RuleError(ValueError):
    """业务规则校验失败。"""


def latest_batch_for_pond(pond: Pond) -> SlakeBatch | None:
    if not pond.batches:
        return None
    return max(pond.batches, key=lambda b: b.started_at)


def can_mark_pond_drawn(pond: Pond) -> tuple[bool, str]:
    """
    熟化池转为「已出灰」(drawn) 的前提：
    最近一条熟化批次的峰值温度已记录，且 >= 60℃。
    """
    latest = latest_batch_for_pond(pond)
    if latest is None:
        return False, "该池尚无熟化批次，不能标记为已出灰"
    if latest.peak_temp_c is None:
        return False, "最近批次尚未记录峰值温度，不能标记为已出灰"
    if latest.peak_temp_c < MIN_PEAK_TEMP_FOR_DRAWN:
        return (
            False,
            f"最近批次峰值温度 {latest.peak_temp_c}℃ 低于 {MIN_PEAK_TEMP_FOR_DRAWN:.0f}℃，不能标记为已出灰",
        )
    return True, ""


def assert_can_record_peak(pond: Pond) -> None:
    """
    峰值温度写入（登记或修正）的前提：池位尚未出灰。
    「已出灰」后峰值永久锁定，任何保存路径都不得再写入。
    """
    if pond.status == Pond.STATUS_DRAWN:
        raise RuleError(f"{pond.code} 已出灰，峰值温度已锁定，不能再登记或修正")


def peak_value_changed(current: float | None, submitted: float | None) -> bool:
    """判断提交的峰值是否真的改动了既有值（原样回传不算写入）。"""
    if current is None or submitted is None:
        return current is not submitted
    return abs(current - submitted) > 1e-9


def assert_can_set_pond_status(pond: Pond, new_status: str) -> None:
    if new_status not in Pond.STATUS_CHOICES:
        raise RuleError(f"无效状态：{new_status}")
    # 「已出灰」是终态：任何保存都不得改回熟化中/注水中，
    # 校验失败后的回滚补偿同样不能绕过这里。
    if pond.status == Pond.STATUS_DRAWN and new_status != Pond.STATUS_DRAWN:
        raise RuleError("池位已出灰，状态已锁定，不能改回其他状态")
    if new_status == Pond.STATUS_DRAWN:
        ok, msg = can_mark_pond_drawn(pond)
        if not ok:
            raise RuleError(msg)

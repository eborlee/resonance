from __future__ import annotations
import asyncio
import hashlib
from typing import List, Dict
from .resonance_combinations import canonical_combo, match_combinations_with_lifecycle, COMBINATION_ROUTING
from .resonance_combinations import ALLOWED_COMBINATIONS
from ..config import settings, universe, routing_rules,get_routing_rules,get_universe
from ..domain.models import (
    Side,
    LevelState,
    TvEvent,
    IntervalState,
    ResonanceSnapshot,
)
from ..infra.utils import ts_to_utc_str
from ..infra.store import AppState
from ..adapters.tg_client import TelegramClient
from .router import (
    choose_topic_by_max_interval,
    max_interval,
    apply_min_interval_floor,
)

import logging
logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)



def filter_by_universe(event: TvEvent) -> TvEvent | None:
    """
    universe 过滤：
    - 不在 universe 的 symbol 直接丢弃
    - interval 不在允许列表的直接丢弃
    """
    allowed_intervals = get_universe().get(event.symbol)
    # allowed_intervals = universe.get(event.symbol)
    if not allowed_intervals:
        
        return None
    for s in event.signals:
        print(s.interval)
    filtered = [s for s in event.signals if s.interval in allowed_intervals]
    
    if not filtered:
        return None

    return TvEvent(symbol=event.symbol, ts=event.ts, signals=filtered)


def format_message(snapshot: ResonanceSnapshot, in_intervals: List[str]) -> str:
    """
    推送内容：只展示“有效参与”的 IN 周
    """
    lines: List[str] = []
    lines.append(f"{snapshot.symbol} {snapshot.side.display}【{len(in_intervals)}】")

    for iv in in_intervals:
        st = snapshot.states.get(iv)
        if st is None:
            continue
        if st.state == LevelState.IN:
            lines.append(f"- {iv}: IN ({st.value:.2f})")
        elif st.state == LevelState.WARM:
            lines.append(f"- {iv}: WARM ({st.value:.2f})")

    return "\n".join(lines)


class ResonanceService:
    """
    多周期共振主逻辑服务：
    - 接收单周期信号
    - 更新缓存状态
    - 根据已处于 IN 的周期组合判断是否推送
    - 匹配 combo 白名单并判断升级
    """

    def __init__(self, state: AppState, tg: TelegramClient):
        self.state = state
        self.tg = tg

    async def handle_event(self, event: TvEvent):
        logger.debug("  \n\n\n\n\n\n\n\n")
        logger.info(f"收到Event数据\nevent:{event}")
        # logger.warning(f"开始处理前的组合cache:{self.state.latest_combo_state}")
        # Step 1️⃣：过滤掉不在 universe 中的 symbol / interval
        event2 = filter_by_universe(event)
        if event2 is None or not event2.signals:
            logger.warning(f"⚠️不在Universe中被定义的标的或窗口! \nEvent信息:{event}")
            return
        # logger.debug(f"step1:过滤不在 universe 中的 symbol / interval后：{event2}")
        # Step 2️⃣：更新状态缓存（AppState），记录最新值和 IN 状态转换
        # intervals_updated = set()
        for sig in event2.signals:
            interval = sig.interval
            value = float(sig.values[0])
            self.state.update_interval(
                symbol=event2.symbol,
                interval=interval,
                value=value,
                ob_level=settings.OB_LEVEL,
                os_level=settings.OS_LEVEL,
                now_ts=event2.ts,  # 使用事件时间戳记录退出时间
            )
            # intervals_updated.add(interval)
        # logger.debug(f"Step2:更新状态缓存：{self.state.cache}")
        allowed_intervals = get_universe().get(event2.symbol,[])
        # allowed_intervals = universe.get(event2.symbol, [])
        if not allowed_intervals:
            return
        # logger.debug(f"系统允许的窗口：{allowed_intervals}")
        # Step 3️⃣：每个方向单独处理（超买/超卖）
        for side in (Side.OVERSOLD, Side.OVERBOUGHT):
            logger.debug(f"============ 开始处理：{side} ============")
            states: Dict[str, IntervalState] = {}
            
            # Step 3.1：构建所有周期的状态字典（IN / WARM / OUT）
            for iv in allowed_intervals:
                rec = self.state.cache.get((event2.symbol, iv))
                if rec is None:
                    st = LevelState.OUT
                    v = 0.0
                else:
                    v = rec.value
                    if rec.in_oversold if side == Side.OVERSOLD else rec.in_overbought:
                        st = LevelState.IN
                    # 此处event2.ts就是推送时间，即k线收盘时间
                    elif self.state.is_warm(event2.symbol, iv, side, now_ts=event2.ts):
                        st = LevelState.WARM
                    # 一旦判定这个窗口为OUT， 立刻重置以此窗口作为最大窗口的组合。
                    else:
                        key = (event2.symbol, side, iv)
                        logger.debug(f"{event2.symbol, iv, side} 为OUT")
                        st = LevelState.OUT
                        self.state.last_active_combo[key] = None

                        # ✅ 在此处立即清理所有 max_iv == 当前 iv 的组合
                        combo_dict = self.state.latest_combo_state[(event2.symbol, side)]
                        for combo, meta in combo_dict.items():
                            if not meta.get("active"):
                                continue
                            if meta.get("max_iv") == iv:
                                logger.warning(f"[失效清理] {event2.symbol}-{side} 组合 {combo} 的最大周期 {iv} 已 OUT，标记为 inactive")
                                meta["active"] = False
                states[iv] = IntervalState(interval=iv, state=st, value=v)
            logger.debug(f"构建的临时字典 表示每个周期状态：{states}")
            # Step 3.2：提取所有处于 IN/WARM 状态的周期
            raw_in_intervals = [
                iv for iv, st in states.items()
                if st.state in (LevelState.IN, LevelState.WARM)
            ]
            logger.debug(f"初步提取的处于IN/WARM状态的周期:{raw_in_intervals}")
            if not raw_in_intervals:
                # 没有任何有效 IN，也要更新共振口径（防止“悬空”）
                self.state.should_emit_resonance(
                    symbol=event2.symbol,
                    side=side,
                    in_count=0,
                    min_resonance=settings.MIN_RESONANCE,
                )
                logger.debug(f"{event2.symbol}-{side}没有任何有效超买超卖窗口")
                continue

            # # Step 3.3：基于 IN 周期计算最大周期（anchor）
            # max_iv = max(raw_in_intervals, key=lambda x: settings.INTERVAL_ORDER.index(x))

            # # Step 3.4：应用 floor 筛选规则（剔除过短周期）
            # effective_in_intervals = apply_min_interval_floor(
            #     in_intervals=raw_in_intervals,
            #     max_iv=max_iv,
            #     max_interval_min_allowed=settings.MAX_INTERVAL_MIN_ALLOWED,
            # )

            in_count = len(raw_in_intervals)
            logger.debug(f"符合状态的窗口数量：{in_count}")
            # Step 3.5：检查是否满足“共振门槛 + 增强 + cooldown”
            # if not self.state.should_emit_resonance(
            #     symbol=event2.symbol,
            #     side=side,
            #     in_count=in_count,
            #     min_resonance=settings.MIN_RESONANCE,
            # ):  
            #     logger.info(f"【门控检查】{event2.symbol}-{side} 未触发任何共振")
            #     continue

            # Step 4️⃣：匹配组合（基于组合白名单 + 生命周期 + 升级）
            # key = (event2.symbol, side, iv)
            # last_active = self.state.last_active_combo.get((event2.symbol, side))

            # combo_results = match_combinations_with_lifecycle(
            #     raw_intervals=raw_in_intervals,
            #     states=states,
            #     pushed_combos=self.state.latest_combo_state[(event2.symbol, side)],
            #     last_active_combo=last_active    
            # )
            combo_results_all = []
            sett = set(
                max_interval(combo) for combo in COMBINATION_ROUTING.keys()
                if all(iv in raw_in_intervals for iv in combo)
            )
            logger.warning(f"set::::{sett}")
            # ★ 核心改动：按 max_iv（= topic）循环
            for max_iv in set(
                max_interval(combo) for combo in COMBINATION_ROUTING.keys()
                if all(iv in raw_in_intervals for iv in combo)
            ):
                if max_iv is None:
                    logger.warning("max_iv计算出错为None")
                    continue
                allowed = [
                    combo for combo in ALLOWED_COMBINATIONS
                    if max_interval(combo) == max_iv
                ]
                
                last_active = self.state.last_active_combo.get(
                    (event2.symbol, side, max_iv)
                )

                combo_results = match_combinations_with_lifecycle(
                    raw_intervals=raw_in_intervals,
                    states=states,
                    pushed_combos=self.state.latest_combo_state[(event2.symbol, side)],
                    last_active_combo=last_active,
                    allowed_combo=allowed
                )

                combo_results_all.extend(combo_results)

            combo_results = combo_results_all
            logger.debug(f"组合得到的共振组合:{combo_results}")


            # #TODO 后期解开推送的逻辑代码后要删除
            # for combo, is_upgrade in combo_results:
            #     canon = canonical_combo(combo)
            #     self.state.latest_combo_state[(event2.symbol, side)][canon] = {
            #             "active": True,
            #             "last_pushed_ts": event2.ts,
            #             "max_iv": canon[0],
            #         }
            # logger.debug(f"最新的self.state.latest_combo_state ：{self.state.latest_combo_state}")
            
            if not combo_results:
                logger.debug(f"{event2.symbol}-{side} 本次无有效组合")
                continue
            
            # Step 5️⃣：逐个组合执行推送
            send_tasks = []

            for combo, is_upgrade in combo_results:
                canon = canonical_combo(combo)
                key = (event2.symbol, side, canon[0])

                # 5.0 防御：必须有 routing
                if canon not in COMBINATION_ROUTING:
                    logger.warning(f"未定义 routing topic 的组合 {canon} 被跳过")
                    continue

                topic_id = COMBINATION_ROUTING[canon]

                # 5.1 构建 snapshot（纯计算）
                sig_str = f"{side.value}|{'|'.join(canon)}"
                signature = hashlib.md5(sig_str.encode("utf-8")).hexdigest()

                snap = ResonanceSnapshot(
                    symbol=event2.symbol,
                    side=side,
                    ts=event2.ts,
                    states=states,
                    score=len(canon),
                    signature=signature,
                )

                msg_prefix = "‼️升级‼️" if is_upgrade else ""
                msg = f"{msg_prefix}\n{ts_to_utc_str(event.ts)}\n {format_message(snap, list(canon))}"

                # ====== 关键点 1：先更新 state（串行、确定性） ======
                self.state.last_active_combo[key] = canon

                self.state.latest_combo_state[(event2.symbol, side)][canon] = {
                    "active": True,
                    "last_pushed_ts": event2.ts,
                    "max_iv": canon[0],
                }

                logger.warning(
                    f"[推送] {event2.symbol}-{side} combo={canon} "
                    f"{'UPGRADE' if is_upgrade else 'NEW'}"
                )

                # ====== 关键点 2：只把 send 动作收集起来 ======
                send_tasks.append(
                    self.tg.send_message(
                        chat_id=settings.TG_CHAT_ID,
                        text=msg,
                        message_thread_id=topic_id,
                    )
                )

            # ====== 关键点 3：for 循环结束后，并发执行外部 IO ======
            if send_tasks:
                await asyncio.gather(*send_tasks, return_exceptions=True)

            # for combo, is_upgrade in combo_results:
            #     canon = canonical_combo(combo)
            #     key = (event2.symbol, side, canon[0])

            #     # 5.0 防御：必须有 routing
            #     if canon not in COMBINATION_ROUTING:
            #         logger.warning(f"未定义 routing topic 的组合 {canon} 被跳过")
            #         continue

            #     topic_id = COMBINATION_ROUTING[canon]

            #     # 5.1 构建 snapshot
            #     sig_str = f"{side.value}|{'|'.join(canon)}"
            #     signature = hashlib.md5(sig_str.encode("utf-8")).hexdigest()

            #     snap = ResonanceSnapshot(
            #         symbol=event2.symbol,
            #         side=side,
            #         ts=event2.ts,
            #         states=states,
            #         score=len(canon),
            #         signature=signature,
            #     )

            #     msg_prefix = "[升级]" if is_upgrade else ""
            #     msg = f"{msg_prefix}\n{ts_to_utc_str(event.ts)}\n {format_message(snap, list(canon))}"

            #     # 5.2 推送
            #     await self.tg.send_message(
            #         chat_id=settings.TG_CHAT_ID,
            #         text=msg,
            #         message_thread_id=topic_id,
            #     )

            #     logger.warning(
            #         f"[推送] {event2.symbol}-{side} combo={canon} "
            #         f"{'UPGRADE' if is_upgrade else 'NEW'}"
            #     )

            #     self.state.last_active_combo[key] = canon


            #     # 5.3 推送成功后，**唯一一次** 更新 combo 状态
            #     self.state.latest_combo_state[(event2.symbol, side)][canon] = {
            #         "active": True,
            #         "last_pushed_ts": event2.ts,
            #         "max_iv": canon[0],
            #     }

# -*- coding: utf-8 -*-
"""Kimi 模型雷达：每分钟监控模型列表的字段变化并推送通知。

监控源：
- kimi.com 模型菜单（GetAvailableModels 的 scene=0 主菜单 / scene=1 Agent 菜单）
  除模型列表外，接口顶层的全局配置（defaultScenario / defaultAgentModel /
  defaultChatModel 等）也纳入对比
- Kimi Code 模型列表（GET {base_url}/v1/models，需在配置中填写 API Key）
- Moonshot 官方模型列表（GET https://api.moonshot.cn/v1/models，需在配置中填写 API Key）

逐模型逐字段深比较，发现任何变化（新增 / 移除 / 字段修改 / 字段增删）
即通过配置的 UMO 发送消息。快照持久化在插件数据目录，重启不丢。
额外数据源连续失败达到阈值时会单独提醒，恢复后再通知一次，避免静默失明。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import aiohttp

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, StarTools

from .watcher_core import (
    API_URL,
    SCENES,
    USER_AGENT,
    build_change_message,
    build_online_message,
    build_source_ready_message,
    diff_models,
    diff_scene,
    models_url,
    split_message,
)

PLUGIN_NAME = "astrbot_plugin_kimi_watcher"
SNAPSHOT_FILE = "snapshot.json"
DEFAULT_KIMI_CODE_URL = "https://api.kimi.com/coding"
DEFAULT_MOONSHOT_URL = "https://api.moonshot.cn/v1"

# 额外数据源：(快照里的键, 通知里的展示名, 默认接口地址)
EXTRA_SOURCE_DEFS = (
    ("kimi_code", "Kimi Code", DEFAULT_KIMI_CODE_URL),
    ("moonshot", "Moonshot 官方", DEFAULT_MOONSHOT_URL),
)

# 连续失败多少次后提醒一次
FAIL_NOTIFY_THRESHOLD = 10

# 额外数据源（Kimi Code / Moonshot 官方）连续失败多少次后单独提醒一次
SOURCE_FAIL_NOTIFY_THRESHOLD = 5


class KimiWatcherPlugin(Star):
    """Kimi 模型雷达插件。"""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self._task: asyncio.Task | None = None
        self._stopped = False
        self._data_dir = None
        self._fail_count = 0
        self._fail_notified = False
        self._source_fail_counts: dict = {}
        self._source_fail_notified: dict = {}

    # ---------------- 生命周期 ----------------

    async def initialize(self) -> None:
        try:
            self._data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[KimiWatcher] 获取插件数据目录失败: {exc}")
            self._data_dir = None
        self._stopped = False
        self._task = asyncio.create_task(self._loop())
        logger.info("[KimiWatcher] 插件已激活，监控任务已启动")

    async def terminate(self) -> None:
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("[KimiWatcher] 插件已停用，监控任务已停止")

    # ---------------- 主循环 ----------------

    async def _loop(self) -> None:
        await asyncio.sleep(3)  # 等平台连接就绪
        try:
            await self._announce_online()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[KimiWatcher] 上线通知发送异常: {exc}")

        while not self._stopped:
            try:
                await self._check_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.error(f"[KimiWatcher] 本轮检查失败: {exc}")
                self._fail_count += 1
                if self._fail_count >= FAIL_NOTIFY_THRESHOLD and not self._fail_notified:
                    self._fail_notified = True
                    await self._notify(
                        "注意：Kimi 模型雷达连续 %d 次拉取接口失败，"
                        "可能是网络波动或接口变动。我会继续重试，恢复后另行通知。"
                        % FAIL_NOTIFY_THRESHOLD
                    )
            else:
                if self._fail_notified:
                    self._fail_notified = False
                    await self._notify("Kimi 模型雷达已恢复连接，继续监视。")
                self._fail_count = 0
            await asyncio.sleep(self._interval())

    def _interval(self) -> int:
        try:
            value = int(self.config.get("interval_seconds", 60))
        except (TypeError, ValueError):
            value = 60
        return max(15, value)

    async def _announce_online(self) -> None:
        """激活时发一条上线通知；无快照时留到首次检查建基线后发。"""
        snapshot = self._load_snapshot()
        if not snapshot or not snapshot.get("scenes"):
            return
        models_by_scene = {
            int(scene): models
            for scene, models in (snapshot.get("scenes") or {}).items()
        }
        extra_sources = [
            (label, (snapshot.get(key) or {}).get("models"))
            for key, label, _url in EXTRA_SOURCE_DEFS
        ]
        await self._notify(
            build_online_message(
                models_by_scene,
                first=False,
                saved_at=snapshot.get("saved_at") or "未知",
                extra_sources=extra_sources,
            )
        )

    def _api_source_conf(self, prefix, default_url):
        """读取某个 OpenAI 兼容数据源的配置；未启用或未填 Key 时返回 None。"""
        if not self.config.get(prefix + "_enable", True):
            return None
        key = str(self.config.get(prefix + "_key") or "").strip()
        if not key:
            return None
        base_url = (
            str(self.config.get(prefix + "_base_url") or "").strip() or default_url
        )
        return base_url, key

    async def _fetch_openai_models(
        self, session: aiohttp.ClientSession, base_url: str, key: str, label: str
    ) -> list:
        headers = {
            "authorization": "Bearer " + key,
            "accept": "application/json",
            "user-agent": USER_AGENT,
        }
        url = models_url(base_url)
        timeout = aiohttp.ClientTimeout(total=20)
        async with session.get(url, headers=headers, timeout=timeout) as resp:
            if resp.status != 200:
                raise RuntimeError(f"{label} 返回 HTTP {resp.status}")
            data = await resp.json(content_type=None)
        models = data.get("data") or []
        if not models:
            raise RuntimeError(f"{label} 返回空模型列表，本轮跳过")
        return models

    async def _check_once(self) -> None:
        async with aiohttp.ClientSession() as session:
            scenes_models = {}
            for scene, _label in SCENES:
                scenes_models[scene] = await self._fetch_scene(session, scene)
            fetched = {}
            for key, label, default_url in EXTRA_SOURCE_DEFS:
                conf = self._api_source_conf(key, default_url)
                if not conf:
                    continue
                try:
                    fetched[key] = await self._fetch_openai_models(
                        session, conf[0], conf[1], label
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[KimiWatcher] {label} 拉取失败（本轮跳过）: {exc}")
                    await self._report_source_failure(key, label, exc)
                else:
                    await self._report_source_recovered(key, label)

        snapshot = self._load_snapshot()
        if not snapshot or not snapshot.get("scenes"):
            self._save_snapshot(scenes_models, fetched)
            await self._notify(
                build_online_message(
                    scenes_models,
                    first=True,
                    extra_sources=[
                        (label, fetched.get(key))
                        for key, label, _url in EXTRA_SOURCE_DEFS
                    ],
                )
            )
            logger.info("[KimiWatcher] 首次运行，已建立基线快照")
            return

        changes = {}
        for scene, label in SCENES:
            old_payload = (snapshot.get("scenes") or {}).get(str(scene))
            diffs = diff_scene(old_payload, scenes_models[scene])
            if diffs:
                changes[label] = diffs

        # 额外数据源：首次拉到先建立基线并通知；之后逐字段对比
        baseline_ready = []
        for key, label, _url in EXTRA_SOURCE_DEFS:
            new_models = fetched.get(key)
            if new_models is None:
                continue
            old_models = (snapshot.get(key) or {}).get("models")
            if old_models is None:
                baseline_ready.append((label, new_models))
            else:
                diffs = diff_models(old_models, new_models)
                if diffs:
                    changes[label] = diffs

        if baseline_ready or changes:
            self._save_snapshot(scenes_models, fetched)
        for label, models in baseline_ready:
            await self._notify(build_source_ready_message(label, models))
            logger.info("[KimiWatcher] %s 基线已建立（%d 个模型）", label, len(models))
        if changes:
            await self._notify(build_change_message(changes))
            logger.info("[KimiWatcher] 检测到变化并已通知: %s", " / ".join(changes))
        else:
            logger.debug("[KimiWatcher] 检查完成，无变化")

    async def _report_source_failure(self, key: str, label: str, exc) -> None:
        """额外数据源连续失败到阈值时通知一次，避免静默失明。"""
        count = self._source_fail_counts.get(key, 0) + 1
        self._source_fail_counts[key] = count
        if count < SOURCE_FAIL_NOTIFY_THRESHOLD or self._source_fail_notified.get(key):
            return
        self._source_fail_notified[key] = True
        await self._notify(
            "注意：%s 连续 %d 次拉取失败（最近一次：%s）。\n"
            "该数据源暂时跳过，其他源照常监视，恢复后我会另行通知。"
            % (label, count, exc)
        )
        logger.warning("[KimiWatcher] %s 连续失败 %d 次，已通知", label, count)

    async def _report_source_recovered(self, key: str, label: str) -> None:
        """额外数据源恢复后，若之前发过失败通知则补发一条恢复通知。"""
        self._source_fail_counts[key] = 0
        if not self._source_fail_notified.pop(key, None):
            return
        await self._notify("%s 拉取已恢复，继续监视。" % label)
        logger.info("[KimiWatcher] %s 拉取已恢复", label)

    async def _fetch_scene(self, session: aiohttp.ClientSession, scene: int) -> dict:
        headers = {
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": USER_AGENT,
        }
        timeout = aiohttp.ClientTimeout(total=20)
        async with session.post(
            API_URL, json={"scene": scene}, headers=headers, timeout=timeout
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"scene={scene} 返回 HTTP {resp.status}")
            data = await resp.json(content_type=None)
        if not (data.get("availableModels") or []):
            raise RuntimeError(f"scene={scene} 返回空模型列表，本轮跳过")
        return data

    # ---------------- 快照持久化 ----------------

    def _snapshot_path(self):
        if self._data_dir is None:
            return None
        return self._data_dir / SNAPSHOT_FILE

    def _load_snapshot(self):
        path = self._snapshot_path()
        if path is None or not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError) as exc:
            logger.warning(f"[KimiWatcher] 快照读取失败，将重建: {exc}")
            return None

    def _save_snapshot(self, scenes_models: dict, sources: dict | None = None) -> None:
        """保存快照；sources 里取不到的数据源保留快照中已有的部分。"""
        path = self._snapshot_path()
        if path is None:
            return
        now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = {
            "saved_at": now_text,
            "scenes": {str(scene): models for scene, models in scenes_models.items()},
        }
        old_snapshot = None
        for key, _label, _url in EXTRA_SOURCE_DEFS:
            models = (sources or {}).get(key)
            if models is None:
                # 本轮未取到该数据源时，保留快照中已有的部分
                if old_snapshot is None:
                    old_snapshot = self._load_snapshot() or {}
                models = (old_snapshot.get(key) or {}).get("models")
            if models is not None:
                payload[key] = {"models": models, "saved_at": now_text}
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=1)
        except OSError as exc:
            logger.error(f"[KimiWatcher] 快照保存失败: {exc}")

    # ---------------- 通知 ----------------

    async def _notify(self, text: str) -> None:
        targets = self.config.get("targets") or []
        if isinstance(targets, str):
            targets = [targets]
        if not targets:
            logger.warning("[KimiWatcher] 未配置通知目标（targets），本次通知跳过")
            return
        for target in targets:
            for chunk in split_message(text):
                ok = await self._send_with_retry(str(target), chunk)
                if not ok:
                    logger.warning(f"[KimiWatcher] 通知发送失败（目标 {target}）")

    async def _send_with_retry(self, target: str, text: str, attempts: int = 3) -> bool:
        for attempt in range(1, attempts + 1):
            try:
                sent = await self.context.send_message(
                    target, MessageChain([Plain(text)])
                )
                if sent:
                    return True
                logger.warning(f"[KimiWatcher] send_message 返回 False（第 {attempt} 次）")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[KimiWatcher] send_message 异常（第 {attempt} 次）: {exc}")
            if attempt < attempts:
                await asyncio.sleep(10)
        return False

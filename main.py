# -*- coding: utf-8 -*-
"""模型雷达：每分钟监控模型列表的字段变化并推送通知。

监控源：
- kimi.com 模型菜单（GetAvailableModels 的 scene=0 主菜单 / scene=1 Agent 菜单）
  除模型列表外，接口顶层的全局配置（defaultScenario / defaultAgentModel /
  defaultChatModel 等）也纳入对比
- Kimi Code 模型列表（GET {base_url}/v1/models，需在配置中填写 API Key）
- Moonshot 官方模型列表（GET https://api.moonshot.cn/v1/models，需在配置中填写 API Key）
- DeepSeek 官方模型列表（GET https://api.deepseek.com/v1/models，需在配置中填写 API Key）

三家额外数据源（Kimi Code / Moonshot / DeepSeek）的聊天回复里还带后端指纹 system_fingerprint，
它不在模型列表里，要发一条极小的流式聊天请求才读得到（会产生少量费用，Kimi Code 占订阅额度），
所以每家按各自单独的间隔探测（设置项 kimi_code_fingerprint_interval_seconds 默认 300 秒、
moonshot_fingerprint_interval_seconds 默认 600 秒、deepseek_fingerprint_interval_seconds 默认 300 秒，填 0 关闭），
探测结果写进对应模型对象，和其他字段一起参与对比。

逐模型逐字段深比较，发现任何变化（新增 / 移除 / 字段修改 / 字段增删）
即通过配置的 UMO 发送消息。快照持久化在插件数据目录，重启不丢。
额外数据源连续失败达到阈值时会单独提醒，恢复后再通知一次，避免静默失明。
指纹探测整轮全部失败连续达到阈值时同样单独提醒（比如 Kimi Code 订阅过期、列表能拉但聊天请求被拒），
恢复后再通知一次。

通知分两路：
- 运维提醒（targets）：插件上线重载、数据源失败恢复等雷达自身状态
- 变化提醒（change_targets）：模型增删、字段变动、新数据源建立基线；
  未配置时回落到 targets，保证旧版本升级后不漏消息
"""

from __future__ import annotations

import asyncio
import json
import time
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
    chat_completions_url,
    diff_models,
    diff_scene,
    models_url,
    split_message,
)

PLUGIN_NAME = "astrbot_plugin_kimi_watcher"
SNAPSHOT_FILE = "snapshot.json"
DEFAULT_KIMI_CODE_URL = "https://api.kimi.com/coding"
DEFAULT_MOONSHOT_URL = "https://api.moonshot.cn/v1"
DEFAULT_DEEPSEEK_URL = "https://api.deepseek.com"

SOURCE_DEEPSEEK = "deepseek"
DEEPSEEK_USER_AGENT = "astrbot-model-radar"
# 额外数据源请求时使用的 User-Agent；没列出的沿用 Kimi 客户端的 UA
SOURCE_USER_AGENTS = {SOURCE_DEEPSEEK: DEEPSEEK_USER_AGENT}

# 额外数据源：(快照里的键, 通知里的展示名, 默认接口地址)
EXTRA_SOURCE_DEFS = (
    ("kimi_code", "Kimi Code", DEFAULT_KIMI_CODE_URL),
    ("moonshot", "Moonshot 官方", DEFAULT_MOONSHOT_URL),
    (SOURCE_DEEPSEEK, "DeepSeek 官方", DEFAULT_DEEPSEEK_URL),
)

# 后端指纹探测间隔（秒）的设置项与默认值，每个数据源各一项：
# Kimi Code 5 分钟、Moonshot 10 分钟、DeepSeek 5 分钟；填 0 关闭，其他值最小 60
FINGERPRINT_INTERVAL_SETTINGS = {
    "kimi_code": ("kimi_code_fingerprint_interval_seconds", 300),
    "moonshot": ("moonshot_fingerprint_interval_seconds", 600),
    SOURCE_DEEPSEEK: ("deepseek_fingerprint_interval_seconds", 300),
}
MIN_FINGERPRINT_INTERVAL = 60

# 连续失败多少次后提醒一次
FAIL_NOTIFY_THRESHOLD = 10

# 额外数据源（Kimi Code / Moonshot 官方 / DeepSeek 官方）连续失败多少次后单独提醒一次
SOURCE_FAIL_NOTIFY_THRESHOLD = 5

# 某数据源的指纹探测连续多少轮「全部失败」后提醒一次（一轮 = 到了探测时间、对每个模型各探测一次）
PROBE_FAIL_NOTIFY_THRESHOLD = 3


class KimiWatcherPlugin(Star):
    """模型雷达插件（Kimi / DeepSeek）。"""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self._task: asyncio.Task | None = None
        self._stopped = False
        self._data_dir = None
        # 各数据源上次探测指纹的时间（time.monotonic），键为数据源键，各自独立计时
        self._last_fingerprint_probe: dict = {}
        self._fail_count = 0
        self._fail_notified = False
        self._source_fail_counts: dict = {}
        self._source_fail_notified: dict = {}
        # 指纹探测的连续失败轮数 / 是否已提醒，键为数据源键
        self._probe_fail_counts: dict = {}
        self._probe_fail_notified: dict = {}

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
                        "注意：模型雷达连续 %d 次拉取接口失败，"
                        "可能是网络波动或接口变动。我会继续重试，恢复后另行通知。"
                        % FAIL_NOTIFY_THRESHOLD
                    )
            else:
                if self._fail_notified:
                    self._fail_notified = False
                    await self._notify("模型雷达已恢复连接，继续监视。")
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
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        key: str,
        label: str,
        user_agent: str = USER_AGENT,
    ) -> list:
        headers = {
            "authorization": "Bearer " + key,
            "accept": "application/json",
            "user-agent": user_agent,
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

    def _fingerprint_interval(self, source: str) -> int:
        """某数据源的指纹探测间隔（秒）：0 表示关闭，其他值不低于 MIN_FINGERPRINT_INTERVAL。"""
        setting, default = FINGERPRINT_INTERVAL_SETTINGS[source]
        try:
            value = int(self.config.get(setting, default))
        except (TypeError, ValueError):
            value = default
        if value <= 0:
            return 0
        return max(MIN_FINGERPRINT_INTERVAL, value)

    async def _fetch_source_models(
        self,
        session: aiohttp.ClientSession,
        source: str,
        base_url: str,
        key: str,
        label: str,
    ) -> list:
        """额外数据源：模型列表每轮都拉（免费），后端指纹按单独间隔探测（花钱 / 占订阅额度）。"""
        user_agent = SOURCE_USER_AGENTS.get(source, USER_AGENT)
        models = await self._fetch_openai_models(
            session, base_url, key, label, user_agent=user_agent
        )
        attempted, succeeded, last_error = await self._attach_fingerprints(
            session, source, base_url, key, models, user_agent
        )
        await self._report_probe_result(source, label, attempted, succeeded, last_error)
        return models

    async def _attach_fingerprints(
        self,
        session: aiohttp.ClientSession,
        source: str,
        base_url: str,
        key: str,
        models: list,
        user_agent: str,
    ) -> tuple:
        """给每个模型补上 system_fingerprint 字段，让它和其他字段一起参与对比。

        没到探测时间、探测失败或回复里没有该字段时，沿用上一轮快照里的值，
        否则会被对比逻辑误判成「字段被删除」。新出现的模型不等间隔，立即探测一次。
        返回 (本轮发起的探测次数, 其中读到指纹的次数, 最近一次失败原因)。
        """
        interval = self._fingerprint_interval(source)
        now = time.monotonic()
        last = self._last_fingerprint_probe.get(source)
        due = interval > 0 and (last is None or now - last >= interval)
        old = (self._load_snapshot() or {}).get(source) or {}
        old_by_id = {m.get("id"): m for m in old.get("models") or []}
        attempted = 0
        succeeded = 0
        last_error = ""
        for model in models:
            model_id = model.get("id")
            if not model_id:
                continue
            fingerprint = None
            if interval > 0 and (due or model_id not in old_by_id):
                attempted += 1
                try:
                    fingerprint = await self._probe_fingerprint(
                        session, base_url, key, model_id, user_agent
                    )
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc) or type(exc).__name__
                    logger.warning(f"[KimiWatcher] {source} 指纹探测失败（{model_id}）: {exc}")
                else:
                    if not fingerprint:
                        last_error = "回复里没有 system_fingerprint"
                if fingerprint:
                    succeeded += 1
            if not fingerprint:
                fingerprint = (old_by_id.get(model_id) or {}).get("system_fingerprint")
            if fingerprint:
                model["system_fingerprint"] = fingerprint
        if due:
            self._last_fingerprint_probe[source] = now
        return attempted, succeeded, last_error

    async def _probe_fingerprint(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        key: str,
        model_id: str,
        user_agent: str,
    ):
        """发一条极小的流式聊天请求，读到第一段带 system_fingerprint 的内容就断开；读不到返回 None。

        Kimi 只有流式回复才带该字段（非流式没有），DeepSeek 两种都带，所以统一走流式。
        """
        headers = {
            "authorization": "Bearer " + key,
            "content-type": "application/json",
            "accept": "text/event-stream",
            "user-agent": user_agent,
        }
        body = {
            "model": model_id,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
            "stream": True,
        }
        timeout = aiohttp.ClientTimeout(total=20)
        async with session.post(
            chat_completions_url(base_url), json=body, headers=headers, timeout=timeout
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            async for raw in resp.content:
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    continue
                fingerprint = chunk.get("system_fingerprint")
                if fingerprint:
                    return fingerprint
        return None

    @staticmethod
    def _seed_fingerprints(old_models: list, new_models: list) -> bool:
        """旧快照里还没有指纹的模型（升级前建的基线），把新探测到的指纹静默并入旧快照。

        否则升级后的第一轮会把每个模型的指纹都报成「新增字段」。返回是否并入过，
        并入过就需要保存一次快照，不然下次旧快照仍没有指纹，真正的指纹变化会被漏掉。
        """
        old_by_id = {m.get("id"): m for m in old_models}
        seeded = False
        for model in new_models:
            old = old_by_id.get(model.get("id"))
            fingerprint = model.get("system_fingerprint")
            if old is not None and fingerprint and "system_fingerprint" not in old:
                old["system_fingerprint"] = fingerprint
                seeded = True
        return seeded

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
                    fetched[key] = await self._fetch_source_models(
                        session, key, conf[0], conf[1], label
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
        seeded = False
        for key, label, _url in EXTRA_SOURCE_DEFS:
            new_models = fetched.get(key)
            if new_models is None:
                continue
            old_models = (snapshot.get(key) or {}).get("models")
            if old_models is None:
                baseline_ready.append((label, new_models))
            else:
                seeded = self._seed_fingerprints(old_models, new_models) or seeded
                diffs = diff_models(old_models, new_models)
                if diffs:
                    changes[label] = diffs

        if baseline_ready or changes or seeded:
            self._save_snapshot(scenes_models, fetched)
        for label, models in baseline_ready:
            await self._notify_change(build_source_ready_message(label, models))
            logger.info("[KimiWatcher] %s 基线已建立（%d 个模型）", label, len(models))
        if changes:
            await self._notify_change(build_change_message(changes))
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

    async def _report_probe_result(
        self, source: str, label: str, attempted: int, succeeded: int, last_error: str
    ) -> None:
        """指纹探测整轮全部失败连续到阈值时通知一次，恢复后再通知一次。

        没发起探测的轮次（没到间隔、已关闭）不计入；一轮里只要有一个模型读到指纹就算成功，
        个别模型读不到不提醒。模型列表照常拉取，所以这里失败只是指纹对比暂时失明，
        沿用旧指纹不会误报。
        """
        if attempted == 0:
            return
        if succeeded > 0:
            self._probe_fail_counts[source] = 0
            if self._probe_fail_notified.pop(source, None):
                await self._notify("%s 的后端指纹探测已恢复，继续监视。" % label)
                logger.info("[KimiWatcher] %s 指纹探测已恢复", label)
            return
        count = self._probe_fail_counts.get(source, 0) + 1
        self._probe_fail_counts[source] = count
        if count < PROBE_FAIL_NOTIFY_THRESHOLD or self._probe_fail_notified.get(source):
            return
        self._probe_fail_notified[source] = True
        await self._notify(
            "注意：%s 的后端指纹探测连续 %d 轮全部失败（最近一次：%s）。\n"
            "模型列表仍在正常监视，只是指纹对比暂时失明（沿用旧指纹，不会误报）；"
            "恢复后我会另行通知。" % (label, count, last_error)
        )
        logger.warning("[KimiWatcher] %s 指纹探测连续 %d 轮全部失败，已通知", label, count)

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

    @staticmethod
    def _read_targets(value):
        if isinstance(value, str):
            value = [value]
        return [str(item) for item in (value or []) if str(item).strip()]

    async def _notify(self, text: str) -> None:
        """运维提醒：雷达自身状态（上线重载、数据源失败恢复）。"""
        targets = self._read_targets(self.config.get("targets"))
        await self._dispatch(text, targets, "运维提醒（targets）")

    async def _notify_change(self, text: str) -> None:
        """变化提醒：模型增删、字段变动、新数据源建立基线。

        change_targets 未配置时回落到 targets，保证升级后不漏消息。
        """
        targets = self._read_targets(self.config.get("change_targets"))
        label = "变化提醒（change_targets）"
        if not targets:
            targets = self._read_targets(self.config.get("targets"))
            label = "变化提醒（回落到 targets）"
        await self._dispatch(text, targets, label)

    async def _dispatch(self, text: str, targets: list, label: str) -> None:
        if not targets:
            logger.warning(f"[KimiWatcher] 未配置通知目标（{label}），本次通知跳过")
            return
        for target in targets:
            for chunk in split_message(text):
                ok = await self._send_with_retry(target, chunk)
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

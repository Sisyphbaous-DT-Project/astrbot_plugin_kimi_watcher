# 模型雷达（astrbot_plugin_kimi_watcher）

每分钟监控模型列表的字段变化并推送通知，覆盖四个监测源：

- kimi.com 模型菜单（GetAvailableModels 的 scene=0 主菜单 / scene=1 Agent 菜单）
- Kimi Code 模型列表（GET {base_url}/v1/models，需在配置中填写 API Key）
- Moonshot 官方模型列表（GET https://api.moonshot.cn/v1/models，需在配置中填写 API Key）
- DeepSeek 官方模型列表（GET https://api.deepseek.com/v1/models，需在配置中填写 API Key）

后三个官方接口（Kimi Code / Moonshot / DeepSeek）还会探测聊天回复里的后端指纹 `system_fingerprint`，作为模型字段一起参与对比。

对每个模型的**所有字段**做逐项深比较，发现任何变化（新增 / 移除 / 字段修改 / 字段增删）
即通过 QQ 发送一条汇总通知。

## 工作方式

- 插件激活后启动后台任务，默认每 60 秒拉取一次接口
- 快照保存在 `data/plugin_data/astrbot_plugin_kimi_watcher/snapshot.json`，重载 / 重启不丢
- 首次运行只建立基线（不误报），并发送一条"已上线"通知
- "已上线 / 已重载上线"通知里，Kimi Code / Moonshot 官方 / DeepSeek 官方的每个模型名后面带当前后端指纹（括号内），例如 `K3（fpv0_57ea5778）`，重载后可以直接确认指纹有没有读到
- Kimi Code / Moonshot 官方 / DeepSeek 官方需在配置中填写 API Key；填好后第一次成功拉取会发送"监测已就绪"通知
- 接口异常时跳过本轮（不更新快照、不误报）；kimi.com 连续失败 10 次发一次提醒，恢复后另行通知
- 额外数据源（Kimi Code / Moonshot 官方 / DeepSeek 官方）连续失败 5 次单独提醒一次，恢复后再通知一次，不会静默失明
- 后端指纹（Kimi Code / Moonshot / DeepSeek）要发一条极小的聊天请求（`max_tokens` 为 1）才能读到：Moonshot、DeepSeek 会产生少量费用，Kimi Code 会占一点订阅额度，所以和免费的模型列表分开计时：列表跟随检查间隔，指纹按各自的间隔探测（Kimi Code 用 `kimi_code_fingerprint_interval_seconds`，默认 300 秒；Moonshot 用 `moonshot_fingerprint_interval_seconds`，默认 600 秒；DeepSeek 用 `deepseek_fingerprint_interval_seconds`，默认 300 秒；填 0 关闭；各数据源独立计时）
- Kimi 只有流式回复才带 `system_fingerprint`（非流式没有），DeepSeek 两种都带，所以探测统一走流式，读到第一段带指纹的内容就断开
- 没到探测时间、探测失败或回复里缺该字段时，沿用上一轮快照里的指纹值，不会被误报成"字段被删除"；新出现的模型不等间隔，立即探测一次
- 升级前已经建好基线的数据源（快照里还没有指纹），第一次读到指纹时静默并入基线，不会把每个模型的指纹都报成"新增字段"
- 后端指纹对应的是后端部署而不是模型名（实测 Moonshot 的 kimi-k3 与 Kimi Code 的 k3 / k3-256k 指纹相同）；指纹变化只代表后端配置有变动，不直接证明模型升级
- 通知分两路：运维提醒（`targets`）发插件上线重载与数据源失败恢复，变化提醒（`change_targets`）发模型增删与字段变动，两者可指向不同会话；`change_targets` 留空时回落 `targets`
- 场景响应的顶层全局字段（defaultScenario / defaultAgentModel / defaultChatModel 等）同样纳入对比
- 长文本字段变化采用"差异聚焦"显示，只展示变化附近的文字（超过 80 字符的文本会裁掉公共部分）
- 模型标识变更（显示名不变）合并为字段变化：`Instant 的标识：k2d6 变为 k2d8`

## 配置

| 配置项 | 说明 |
| --- | --- |
| `targets` | 运维提醒目标 UMO 列表（必填）：插件上线重载、数据源失败与恢复等雷达自身状态发到这里。格式 `platform:type:session_id`，例如 `default:FriendMessage:100000000`；不填则不会发送通知 |
| `change_targets` | 变化提醒目标 UMO 列表：模型增删、字段变动、新数据源建立基线发到这里，可与运维提醒分开放不同会话；留空则回落到 `targets` |
| `interval_seconds` | 检查间隔（秒），默认 60，最小 15；只管模型列表的拉取（不花钱） |
| `kimi_code_fingerprint_interval_seconds` | Kimi Code 后端指纹探测间隔（秒），默认 300，最小 60，填 0 关闭 Kimi Code 的指纹探测（模型列表监测不受影响）；会占订阅额度 |
| `moonshot_fingerprint_interval_seconds` | Moonshot 官方后端指纹探测间隔（秒），默认 600，最小 60，填 0 关闭 Moonshot 的指纹探测（模型列表监测不受影响）；会产生少量费用 |
| `deepseek_fingerprint_interval_seconds` | DeepSeek 后端指纹探测间隔（秒），默认 300，最小 60，填 0 关闭 DeepSeek 的指纹探测（模型列表监测不受影响）；会产生少量费用 |
| `kimi_code_enable` | 是否监测 Kimi Code，默认开启 |
| `kimi_code_base_url` | Kimi Code 接口地址，默认 `https://api.kimi.com/coding` |
| `kimi_code_key` | Kimi Code API Key（自己填，形如 `sk-kimi-xxx`）；不填则跳过 Kimi Code 监测 |
| `moonshot_enable` | 是否监测 Moonshot 官方模型列表，默认开启 |
| `moonshot_base_url` | Moonshot 官方接口地址，默认 `https://api.moonshot.cn/v1` |
| `moonshot_key` | Moonshot 官方 API Key（自己填，形如 `sk-xxx`）；不填则跳过 Moonshot 官方监测 |
| `deepseek_enable` | 是否监测 DeepSeek 官方模型列表，默认开启 |
| `deepseek_base_url` | DeepSeek 官方接口地址，默认 `https://api.deepseek.com` |
| `deepseek_key` | DeepSeek 官方 API Key（自己填，形如 `sk-xxx`）；不填则跳过 DeepSeek 官方监测 |

## 更新记录

### v1.8.0 (2026-10-17)

- 新增 DeepSeek 官方模型列表监测（GET /v1/models，全字段对比）：模型 id、名称、归属方、上下文窗口、最大输出、输入 / 输出类型、思考档位、Anthropic 协议能力等字段的变化都会通知
- 新增后端指纹 `system_fingerprint` 监测，覆盖 Kimi Code / Moonshot / DeepSeek 三家：向每个模型发一条极小的流式聊天请求读取（Kimi 只有流式回复才带该字段），作为模型字段参与对比；与模型列表分开计时，Kimi Code 默认每 300 秒一次、Moonshot 默认每 600 秒一次、DeepSeek 默认每 300 秒一次，可在设置页分别修改，填 0 关闭
- 新增配置项 `deepseek_enable` / `deepseek_base_url` / `deepseek_key`（Key 由使用者自行填写，插件不内置任何凭据），以及三家各自的指纹探测间隔 `kimi_code_fingerprint_interval_seconds` / `moonshot_fingerprint_interval_seconds` / `deepseek_fingerprint_interval_seconds`
- 指纹探测失败、未到探测时间时沿用上一轮的值，不误报；新模型立即探测一次；升级前已建基线的数据源第一次读到指纹时静默并入基线，不会报"新增字段"
- "已上线 / 已重载上线"通知里，三家官方数据源的每个模型名后面带上当前后端指纹
- 通知标题与插件显示名去掉"Kimi"，改为"模型有新变化""模型雷达"，覆盖四个监测源
- DeepSeek 官方首次建立基线时发送"监测已就绪"通知

### v1.7.0 (2026-09-26)

- 新增配置项 `change_targets`：通知目标拆分为两路，运维提醒（`targets`）与变化提醒（`change_targets`）可发送到不同会话
- 变化提醒覆盖：模型增删、字段变动、新数据源建立基线；运维提醒覆盖：插件上线重载、数据源连续失败与恢复
- `change_targets` 留空时自动回落到 `targets`，旧配置升级后行为不变、不会漏消息

### v1.6.0 (2026-09-23)

- 新增监控接口顶层的全局配置字段（defaultScenario / defaultAgentModel / defaultChatModel 等），此前只比较 availableModels 数组，这些字段的变化看不见
- 新增额外数据源（Kimi Code / Moonshot 官方）失效通知：连续 5 次拉取失败提醒一次，恢复后再通知一次；此前失败只写日志、静默跳过
- 快照升级为完整响应对象，旧版快照（裸数组）自动兼容；升级后首轮会提示一次顶层字段，之后不再重复
- 顶层字段补充中文名：默认场景 / 默认 Agent 模型 / 默认聊天模型

### v1.5.1 (2026-09-13)

- 修复：Moonshot 官方接口的 created 字段每次请求都会漂移若干秒（接口侧的列表生成时间，非模型属性），导致每分钟误报一次；该字段已排除出对比，新模型上架仍由「新增模型」正常捕获

### v1.5.0 (2026-09-13)

- 新增 Moonshot 官方模型列表监测（GET /v1/models，全字段对比）
- 新增配置项 moonshot_enable / moonshot_base_url / moonshot_key（Key 由使用者自行填写，插件不内置任何凭据）
- Moonshot 官方首次建立基线时发送「监测已就绪」通知
- 空字符串字段值在提示中显示为「（空）」，长文本聚焦逻辑跳过空串

### v1.4.0 (2026-09-12)

- 新增 Kimi Code 模型列表监测（GET /v1/models，全字段对比）
- 新增配置项 kimi_code_enable / kimi_code_base_url / kimi_code_key（Key 由使用者自行填写，插件不内置任何凭据）
- Kimi Code 首次建立基线时发送"监测已就绪"通知
- 通知标题调整为"Kimi 模型有新变化"，覆盖两个监测源

### v1.3.0 (2026-09-12)

- 新增插件图标 logo.png（256x256）
- 移除内置的默认通知目标：必须在配置里填写 `targets` 才会发送通知
- 未配置 targets 时跳过发送并在日志中提示

### v1.1.0 (2026-09-12)

- 长文本字段变化改为"差异聚焦"显示，直接看到变化附近的文字
- 模型标识变更（显示名不变）合并为字段变化，不再报为"新增 + 移除"
- 全部通知文案移除表情符号，改为纯文字
- 新增 greeting 字段的中文名（开场白）

### v1.0.0

- 首个版本：模型菜单全字段监控 + QQ 通知

## 字段覆盖与提示样例

- 数据：真实接口数据（kimi.com 模型菜单 + Kimi Code 模型列表 + Moonshot 官方模型列表 + DeepSeek 官方模型列表及探测到的后端指纹）
- 方法：对每个字段逐个篡改（文本加后缀、布尔取反等），记录插件会发出的提示
- 结果：kimi.com 主菜单 62 个 + Agent 菜单 83 个字段实例；Kimi Code 59 个；Moonshot 官方 72 个；接口顶层全局字段 4 个；DeepSeek 官方 29 个（含后端指纹）；合计 309 个，全部检出（0 漏检）
- 说明：示例中的"（改）"为测试时加的篡改标记

### 一、kimi.com 模型菜单

#### 按字段类型（每类一个例子）

1. **scenario**（6 处）

   示例（主菜单 · Instant）：
   Instant 的场景：SCENARIO_K2D5 变为 SCENARIO_K2D5（改）

2. **displayName**（6 处）

   示例（主菜单 · Instant）：
   Instant（改） 的显示名：Instant 变为 Instant（改）

3. **description**（6 处）

   示例（主菜单 · Instant）：
   Instant 的描述：Fast chat, quick replies 变为 Fast chat, quick replies（改）

4. **inputPlaceholder**（6 处）

   示例（主菜单 · Instant）：
   Instant 的输入提示：Ask anything. Images work too. 变为 Ask anything. Images work too.（改）

5. **key**（6 处）

   示例（主菜单 · Instant）：
   Instant 的标识：k2d6 变为 k2d6（改）

6. **reasoningEffortOptions[0].effort**（17 处）

   示例（主菜单 · Instant）：
   Instant 的思考档位[0].档位：REASONING_EFFORT_NONE 变为 REASONING_EFFORT_NONE（改）

7. **reasoningEffortOptions[0].displayName**（17 处）

   示例（主菜单 · Instant）：
   Instant 的思考档位[0].显示名：Standard 变为 Standard（改）

8. **defaultReasoningEffort**（6 处）

   示例（主菜单 · Instant）：
   Instant 的默认思考档位：REASONING_EFFORT_LOW 变为 REASONING_EFFORT_LOW（改）

9. **kimiPlusId**（5 处）

   示例（主菜单 · K3）：
   K3 的 Kimi+ 标识：ok-computer 变为 ok-computer（改）

10. **agentMode**（5 处）

   示例（主菜单 · K3）：
   K3 的 Agent 模式：TYPE_NORMAL 变为 TYPE_NORMAL（改）

11. **switchableTo[0]**（4 处）

   示例（主菜单 · K3）：
   K3 的可切换至[0]：k3-agent-ultra 变为 k3-agent-ultra（改）

12. **reasoningEffortOptions[2].description**（5 处）

   示例（主菜单 · K3）：
   K3 的思考档位[2].描述：Consumes more credits 变为 Consumes more credits（改）

13. **contextLengthOptions[0].contextLength**（10 处）

   示例（主菜单 · K3）：
   K3 的上下文档位[0].档位：CONTEXT_LENGTH_L 变为 CONTEXT_LENGTH_L（改）

14. **contextLengthOptions[0].displayName**（10 处）

   示例（主菜单 · K3）：
   K3 的上下文档位[0].显示名：Standard 变为 Standard（改）

15. **contextLengthOptions[0].available**（5 处）

   示例（主菜单 · K3）：
   K3 的上下文档位[0].可用：是 变为 否

16. **contextLengthOptions[1].description**（5 处）

   示例（主菜单 · K3）：
   K3 的上下文档位[1].描述：Up to 1M tokens; consumes more credits 变为 Up to 1M tokens; consumes more credits（改）

17. **contextLengthOptions[1].label**（5 处）

   示例（主菜单 · K3）：
   K3 的上下文档位[1].标签：Allegro plan only 变为 Allegro plan only（改）

18. **contextLengthOptions[1].minMembershipLevel**（5 处）

   示例（主菜单 · K3）：
   K3 的上下文档位[1].会员门槛：LEVEL_ADVANCED 变为 LEVEL_ADVANCED（改）

19. **defaultContextLength**（5 处）

   示例（主菜单 · K3）：
   K3 的默认上下文档位：CONTEXT_LENGTH_L 变为 CONTEXT_LENGTH_L（改）

20. **minMembershipLevel**（1 处）

   示例（主菜单 · K3 Swarm）：
   K3 Swarm 的会员门槛：LEVEL_BASIC 变为 LEVEL_BASIC（改）

21. **greeting**（1 处）

   示例（主菜单 · K3 Swarm）：
   K3 Swarm 的开场白：…ultiple outputs across formats 变为 …ultiple outputs across formats（改）

22. **modelId**（3 处）

   示例（Agent 菜单 · K3）：
   K3 的模型 ID：k3-agent 变为 k3-agent（改）

#### 全部实例（按模型分组）

##### 主菜单 · Instant（10 个字段）

- Instant 的场景：SCENARIO_K2D5 变为 SCENARIO_K2D5（改）
- Instant（改） 的显示名：Instant 变为 Instant（改）
- Instant 的描述：Fast chat, quick replies 变为 Fast chat, quick replies（改）
- Instant 的输入提示：Ask anything. Images work too. 变为 Ask anything. Images work too.（改）
- Instant 的标识：k2d6 变为 k2d6（改）
- Instant 的思考档位[0].档位：REASONING_EFFORT_NONE 变为 REASONING_EFFORT_NONE（改）
- Instant 的思考档位[0].显示名：Standard 变为 Standard（改）
- Instant 的思考档位[1].档位：REASONING_EFFORT_LOW 变为 REASONING_EFFORT_LOW（改）
- Instant 的思考档位[1].显示名：High 变为 High（改）
- Instant 的默认思考档位：REASONING_EFFORT_LOW 变为 REASONING_EFFORT_LOW（改）

##### 主菜单 · K3（25 个字段）

- K3 的场景：SCENARIO_OK_COMPUTER 变为 SCENARIO_OK_COMPUTER（改）
- K3（改） 的显示名：K3 变为 K3（改）
- K3 的描述：Chat & Agent, flagship all-rounder 变为 Chat & Agent, flagship all-rounder（改）
- K3 的 Kimi+ 标识：ok-computer 变为 ok-computer（改）
- K3 的 Agent 模式：TYPE_NORMAL 变为 TYPE_NORMAL（改）
- K3 的输入提示：Ask anything, or task an agent... 变为 Ask anything, or task an agent...（改）
- K3 的标识：k3 变为 k3（改）
- K3 的可切换至[0]：k3-agent-ultra 变为 k3-agent-ultra（改）
- K3 的思考档位[0].档位：REASONING_EFFORT_LOW 变为 REASONING_EFFORT_LOW（改）
- K3 的思考档位[0].显示名：Standard 变为 Standard（改）
- K3 的思考档位[1].档位：REASONING_EFFORT_HIGH 变为 REASONING_EFFORT_HIGH（改）
- K3 的思考档位[1].显示名：High 变为 High（改）
- K3 的思考档位[2].档位：REASONING_EFFORT_MAX 变为 REASONING_EFFORT_MAX（改）
- K3 的思考档位[2].显示名：Max 变为 Max（改）
- K3 的思考档位[2].描述：Consumes more credits 变为 Consumes more credits（改）
- K3 的默认思考档位：REASONING_EFFORT_HIGH 变为 REASONING_EFFORT_HIGH（改）
- K3 的上下文档位[0].档位：CONTEXT_LENGTH_L 变为 CONTEXT_LENGTH_L（改）
- K3 的上下文档位[0].显示名：Standard 变为 Standard（改）
- K3 的上下文档位[0].可用：是 变为 否
- K3 的上下文档位[1].档位：CONTEXT_LENGTH_XL 变为 CONTEXT_LENGTH_XL（改）
- K3 的上下文档位[1].显示名：Extra Long 变为 Extra Long（改）
- K3 的上下文档位[1].描述：Up to 1M tokens; consumes more credits 变为 Up to 1M tokens; consumes more credits（改）
- K3 的上下文档位[1].标签：Allegro plan only 变为 Allegro plan only（改）
- K3 的上下文档位[1].会员门槛：LEVEL_ADVANCED 变为 LEVEL_ADVANCED（改）
- K3 的默认上下文档位：CONTEXT_LENGTH_L 变为 CONTEXT_LENGTH_L（改）

##### 主菜单 · K3 Swarm（27 个字段）

- K3 Swarm 的场景：SCENARIO_OK_COMPUTER 变为 SCENARIO_OK_COMPUTER（改）
- K3 Swarm（改） 的显示名：K3 Swarm 变为 K3 Swarm（改）
- K3 Swarm 的描述：Massive search, batch processing, and more in one go 变为 Massive search, batch processing, and more in one go（改）
- K3 Swarm 的 Kimi+ 标识：ok-computer 变为 ok-computer（改）
- K3 Swarm 的 Agent 模式：TYPE_ULTRA 变为 TYPE_ULTRA（改）
- K3 Swarm 的输入提示：Assign a task to your AI team... 变为 Assign a task to your AI team...（改）
- K3 Swarm 的标识：k3-agent-ultra 变为 k3-agent-ultra（改）
- K3 Swarm 的可切换至[0]：k3 变为 k3（改）
- K3 Swarm 的思考档位[0].档位：REASONING_EFFORT_LOW 变为 REASONING_EFFORT_LOW（改）
- K3 Swarm 的思考档位[0].显示名：Standard 变为 Standard（改）
- K3 Swarm 的思考档位[1].档位：REASONING_EFFORT_HIGH 变为 REASONING_EFFORT_HIGH（改）
- K3 Swarm 的思考档位[1].显示名：High 变为 High（改）
- K3 Swarm 的思考档位[2].档位：REASONING_EFFORT_MAX 变为 REASONING_EFFORT_MAX（改）
- K3 Swarm 的思考档位[2].显示名：Max 变为 Max（改）
- K3 Swarm 的思考档位[2].描述：Consumes more credits 变为 Consumes more credits（改）
- K3 Swarm 的默认思考档位：REASONING_EFFORT_HIGH 变为 REASONING_EFFORT_HIGH（改）
- K3 Swarm 的上下文档位[0].档位：CONTEXT_LENGTH_L 变为 CONTEXT_LENGTH_L（改）
- K3 Swarm 的上下文档位[0].显示名：Standard 变为 Standard（改）
- K3 Swarm 的上下文档位[0].可用：是 变为 否
- K3 Swarm 的上下文档位[1].档位：CONTEXT_LENGTH_XL 变为 CONTEXT_LENGTH_XL（改）
- K3 Swarm 的上下文档位[1].显示名：Extra Long 变为 Extra Long（改）
- K3 Swarm 的上下文档位[1].描述：Up to 1M tokens; consumes more credits 变为 Up to 1M tokens; consumes more credits（改）
- K3 Swarm 的上下文档位[1].标签：Allegro plan only 变为 Allegro plan only（改）
- K3 Swarm 的上下文档位[1].会员门槛：LEVEL_ADVANCED 变为 LEVEL_ADVANCED（改）
- K3 Swarm 的默认上下文档位：CONTEXT_LENGTH_L 变为 CONTEXT_LENGTH_L（改）
- K3 Swarm 的会员门槛：LEVEL_BASIC 变为 LEVEL_BASIC（改）
- K3 Swarm 的开场白：…ultiple outputs across formats 变为 …ultiple outputs across formats（改）

##### Agent 菜单 · K3（26 个字段）

- K3 的场景：SCENARIO_OK_COMPUTER 变为 SCENARIO_OK_COMPUTER（改）
- K3（改） 的显示名：K3 变为 K3（改）
- K3 的描述：Chat & Agent, flagship all-rounder 变为 Chat & Agent, flagship all-rounder（改）
- K3 的 Kimi+ 标识：ok-computer 变为 ok-computer（改）
- K3 的 Agent 模式：TYPE_NORMAL 变为 TYPE_NORMAL（改）
- K3 的输入提示：Ask anything, or task an agent... 变为 Ask anything, or task an agent...（改）
- K3 的标识：k3-agent 变为 k3-agent（改）
- K3 的可切换至[0]：k3-agent-ultra 变为 k3-agent-ultra（改）
- K3 的思考档位[0].档位：REASONING_EFFORT_LOW 变为 REASONING_EFFORT_LOW（改）
- K3 的思考档位[0].显示名：Standard 变为 Standard（改）
- K3 的思考档位[1].档位：REASONING_EFFORT_HIGH 变为 REASONING_EFFORT_HIGH（改）
- K3 的思考档位[1].显示名：High 变为 High（改）
- K3 的思考档位[2].档位：REASONING_EFFORT_MAX 变为 REASONING_EFFORT_MAX（改）
- K3 的思考档位[2].显示名：Max 变为 Max（改）
- K3 的思考档位[2].描述：Consumes more credits 变为 Consumes more credits（改）
- K3 的默认思考档位：REASONING_EFFORT_HIGH 变为 REASONING_EFFORT_HIGH（改）
- K3 的模型 ID：k3-agent 变为 k3-agent（改）
- K3 的上下文档位[0].档位：CONTEXT_LENGTH_L 变为 CONTEXT_LENGTH_L（改）
- K3 的上下文档位[0].显示名：Standard 变为 Standard（改）
- K3 的上下文档位[0].可用：是 变为 否
- K3 的上下文档位[1].档位：CONTEXT_LENGTH_XL 变为 CONTEXT_LENGTH_XL（改）
- K3 的上下文档位[1].显示名：Extra Long 变为 Extra Long（改）
- K3 的上下文档位[1].描述：Up to 1M tokens; consumes more credits 变为 Up to 1M tokens; consumes more credits（改）
- K3 的上下文档位[1].标签：Allegro plan only 变为 Allegro plan only（改）
- K3 的上下文档位[1].会员门槛：LEVEL_ADVANCED 变为 LEVEL_ADVANCED（改）
- K3 的默认上下文档位：CONTEXT_LENGTH_L 变为 CONTEXT_LENGTH_L（改）

##### Agent 菜单 · K3 Swarm（26 个字段）

- K3 Swarm 的场景：SCENARIO_OK_COMPUTER 变为 SCENARIO_OK_COMPUTER（改）
- K3 Swarm（改） 的显示名：K3 Swarm 变为 K3 Swarm（改）
- K3 Swarm 的描述：Massive search, batch processing, and more in one go 变为 Massive search, batch processing, and more in one go（改）
- K3 Swarm 的 Kimi+ 标识：ok-computer 变为 ok-computer（改）
- K3 Swarm 的 Agent 模式：TYPE_ULTRA 变为 TYPE_ULTRA（改）
- K3 Swarm 的输入提示：Assign a task to your AI team... 变为 Assign a task to your AI team...（改）
- K3 Swarm 的标识：k3-agent-ultra 变为 k3-agent-ultra（改）
- K3 Swarm 的可切换至[0]：k3-agent 变为 k3-agent（改）
- K3 Swarm 的思考档位[0].档位：REASONING_EFFORT_LOW 变为 REASONING_EFFORT_LOW（改）
- K3 Swarm 的思考档位[0].显示名：Standard 变为 Standard（改）
- K3 Swarm 的思考档位[1].档位：REASONING_EFFORT_HIGH 变为 REASONING_EFFORT_HIGH（改）
- K3 Swarm 的思考档位[1].显示名：High 变为 High（改）
- K3 Swarm 的思考档位[2].档位：REASONING_EFFORT_MAX 变为 REASONING_EFFORT_MAX（改）
- K3 Swarm 的思考档位[2].显示名：Max 变为 Max（改）
- K3 Swarm 的思考档位[2].描述：Consumes more credits 变为 Consumes more credits（改）
- K3 Swarm 的默认思考档位：REASONING_EFFORT_HIGH 变为 REASONING_EFFORT_HIGH（改）
- K3 Swarm 的模型 ID：k3-agent-swarm 变为 k3-agent-swarm（改）
- K3 Swarm 的上下文档位[0].档位：CONTEXT_LENGTH_L 变为 CONTEXT_LENGTH_L（改）
- K3 Swarm 的上下文档位[0].显示名：Standard 变为 Standard（改）
- K3 Swarm 的上下文档位[0].可用：是 变为 否
- K3 Swarm 的上下文档位[1].档位：CONTEXT_LENGTH_XL 变为 CONTEXT_LENGTH_XL（改）
- K3 Swarm 的上下文档位[1].显示名：Extra Long 变为 Extra Long（改）
- K3 Swarm 的上下文档位[1].描述：Up to 1M tokens; consumes more credits 变为 Up to 1M tokens; consumes more credits（改）
- K3 Swarm 的上下文档位[1].标签：Allegro plan only 变为 Allegro plan only（改）
- K3 Swarm 的上下文档位[1].会员门槛：LEVEL_ADVANCED 变为 LEVEL_ADVANCED（改）
- K3 Swarm 的默认上下文档位：CONTEXT_LENGTH_L 变为 CONTEXT_LENGTH_L（改）

##### Agent 菜单 · K2.8 Preview（25 个字段）

- K2.8 Preview 的场景：SCENARIO_OK_COMPUTER 变为 SCENARIO_OK_COMPUTER（改）
- K2.8 Preview（改） 的显示名：K2.8 Preview 变为 K2.8 Preview（改）
- K2.8 Preview 的描述：Well-rounded and efficient for everyday tasks 变为 Well-rounded and efficient for everyday tasks（改）
- K2.8 Preview 的 Kimi+ 标识：ok-computer 变为 ok-computer（改）
- K2.8 Preview 的 Agent 模式：TYPE_NORMAL 变为 TYPE_NORMAL（改）
- K2.8 Preview 的输入提示：Ask anything, or task an agent... 变为 Ask anything, or task an agent...（改）
- K2.8 Preview 的标识：k28-preview 变为 k28-preview（改）
- K2.8 Preview 的思考档位[0].档位：REASONING_EFFORT_LOW 变为 REASONING_EFFORT_LOW（改）
- K2.8 Preview 的思考档位[0].显示名：Standard 变为 Standard（改）
- K2.8 Preview 的思考档位[1].档位：REASONING_EFFORT_HIGH 变为 REASONING_EFFORT_HIGH（改）
- K2.8 Preview 的思考档位[1].显示名：High 变为 High（改）
- K2.8 Preview 的思考档位[2].档位：REASONING_EFFORT_MAX 变为 REASONING_EFFORT_MAX（改）
- K2.8 Preview 的思考档位[2].显示名：Max 变为 Max（改）
- K2.8 Preview 的思考档位[2].描述：Consumes more credits 变为 Consumes more credits（改）
- K2.8 Preview 的默认思考档位：REASONING_EFFORT_HIGH 变为 REASONING_EFFORT_HIGH（改）
- K2.8 Preview 的模型 ID：k28-agent-preview 变为 k28-agent-preview（改）
- K2.8 Preview 的上下文档位[0].档位：CONTEXT_LENGTH_L 变为 CONTEXT_LENGTH_L（改）
- K2.8 Preview 的上下文档位[0].显示名：Standard 变为 Standard（改）
- K2.8 Preview 的上下文档位[0].可用：是 变为 否
- K2.8 Preview 的上下文档位[1].档位：CONTEXT_LENGTH_XL 变为 CONTEXT_LENGTH_XL（改）
- K2.8 Preview 的上下文档位[1].显示名：Extra Long 变为 Extra Long（改）
- K2.8 Preview 的上下文档位[1].描述：Up to 1M tokens; consumes more credits 变为 Up to 1M tokens; consumes more credits（改）
- K2.8 Preview 的上下文档位[1].标签：Allegro plan only 变为 Allegro plan only（改）
- K2.8 Preview 的上下文档位[1].会员门槛：LEVEL_ADVANCED 变为 LEVEL_ADVANCED（改）
- K2.8 Preview 的默认上下文档位：CONTEXT_LENGTH_L 变为 CONTEXT_LENGTH_L（改）

### 二、Kimi Code 模型列表

#### 按字段类型（每类一个例子）

1. **id**（4 处）

   示例（Kimi Code · K2.8 Preview）：
   K2.8 Preview 的模型 ID：kimi-for-coding 变为 kimi-for-coding（改）

2. **created**（4 处）

   示例（Kimi Code · K2.8 Preview）：
   K2.8 Preview 的创建时间戳：1761264000 变为 1761264001

3. **created_at**（4 处）

   示例（Kimi Code · K2.8 Preview）：
   K2.8 Preview 的创建时间：2025-10-24T00:00:00Z 变为 2025-10-24T00:00:00Z（改）

4. **object**（4 处）

   示例（Kimi Code · K2.8 Preview）：
   K2.8 Preview 的对象类型：model 变为 model（改）

5. **display_name**（4 处）

   示例（Kimi Code · K2.8 Preview）：
   K2.8 Preview（改） 的显示名：K2.8 Preview 变为 K2.8 Preview（改）

6. **type**（4 处）

   示例（Kimi Code · K2.8 Preview）：
   K2.8 Preview 的类型：model 变为 model（改）

7. **context_length**（4 处）

   示例（Kimi Code · K2.8 Preview）：
   K2.8 Preview 的上下文长度：1048576 变为 1048577

8. **supports_reasoning**（4 处）

   示例（Kimi Code · K2.8 Preview）：
   K2.8 Preview 的支持推理：是 变为 否

9. **supports_image_in**（4 处）

   示例（Kimi Code · K2.8 Preview）：
   K2.8 Preview 的支持图片输入：是 变为 否

10. **supports_video_in**（4 处）

   示例（Kimi Code · K2.8 Preview）：
   K2.8 Preview 的支持视频输入：是 变为 否

11. **supports_dynamic_tools**（4 处）

   示例（Kimi Code · K2.8 Preview）：
   K2.8 Preview 的支持动态工具：是 变为 否

12. **supports_thinking_type**（4 处）

   示例（Kimi Code · K2.8 Preview）：
   K2.8 Preview 的思考模式类型：only 变为 only（改）

13. **think_efforts.support**（3 处）

   示例（Kimi Code · K2.8 Preview）：
   K2.8 Preview 的思考档位设置.支持：是 变为 否

14. **think_efforts.valid_efforts[0]**（9 处）

   示例（Kimi Code · K2.8 Preview）：
   K2.8 Preview 的思考档位设置.有效档位[0]：low 变为 low（改）

15. **think_efforts.default_effort**（3 处）

   示例（Kimi Code · K2.8 Preview）：
   K2.8 Preview 的思考档位设置.默认档位：max 变为 max（改）

#### 全部实例（按模型分组）

##### Kimi Code · K2.8 Preview（17 个字段）

- K2.8 Preview 的模型 ID：kimi-for-coding 变为 kimi-for-coding（改）
- K2.8 Preview 的创建时间戳：1761264000 变为 1761264001
- K2.8 Preview 的创建时间：2025-10-24T00:00:00Z 变为 2025-10-24T00:00:00Z（改）
- K2.8 Preview 的对象类型：model 变为 model（改）
- K2.8 Preview（改） 的显示名：K2.8 Preview 变为 K2.8 Preview（改）
- K2.8 Preview 的类型：model 变为 model（改）
- K2.8 Preview 的上下文长度：1048576 变为 1048577
- K2.8 Preview 的支持推理：是 变为 否
- K2.8 Preview 的支持图片输入：是 变为 否
- K2.8 Preview 的支持视频输入：是 变为 否
- K2.8 Preview 的支持动态工具：是 变为 否
- K2.8 Preview 的思考模式类型：only 变为 only（改）
- K2.8 Preview 的思考档位设置.支持：是 变为 否
- K2.8 Preview 的思考档位设置.有效档位[0]：low 变为 low（改）
- K2.8 Preview 的思考档位设置.有效档位[1]：high 变为 high（改）
- K2.8 Preview 的思考档位设置.有效档位[2]：max 变为 max（改）
- K2.8 Preview 的思考档位设置.默认档位：max 变为 max（改）

##### Kimi Code · K2.7 Code Highspeed（12 个字段）

- K2.7 Code Highspeed 的模型 ID：kimi-for-coding-highspeed 变为 kimi-for-coding-highspeed（改）
- K2.7 Code Highspeed 的创建时间戳：1761264000 变为 1761264001
- K2.7 Code Highspeed 的创建时间：2025-10-24T00:00:00Z 变为 2025-10-24T00:00:00Z（改）
- K2.7 Code Highspeed 的对象类型：model 变为 model（改）
- K2.7 Code Highspeed（改） 的显示名：K2.7 Code Highspeed 变为 K2.7 Code Highspeed（改）
- K2.7 Code Highspeed 的类型：model 变为 model（改）
- K2.7 Code Highspeed 的上下文长度：262144 变为 262145
- K2.7 Code Highspeed 的支持推理：是 变为 否
- K2.7 Code Highspeed 的支持图片输入：是 变为 否
- K2.7 Code Highspeed 的支持视频输入：是 变为 否
- K2.7 Code Highspeed 的支持动态工具：否 变为 是
- K2.7 Code Highspeed 的思考模式类型：only 变为 only（改）

##### Kimi Code · K3（17 个字段）

- K3 的模型 ID：k3 变为 k3（改）
- K3 的创建时间戳：1761264000 变为 1761264001
- K3 的创建时间：2025-10-24T00:00:00Z 变为 2025-10-24T00:00:00Z（改）
- K3 的对象类型：model 变为 model（改）
- K3（改） 的显示名：K3 变为 K3（改）
- K3 的类型：model 变为 model（改）
- K3 的上下文长度：1048576 变为 1048577
- K3 的支持推理：是 变为 否
- K3 的支持图片输入：是 变为 否
- K3 的支持视频输入：是 变为 否
- K3 的支持动态工具：是 变为 否
- K3 的思考模式类型：only 变为 only（改）
- K3 的思考档位设置.支持：是 变为 否
- K3 的思考档位设置.有效档位[0]：low 变为 low（改）
- K3 的思考档位设置.有效档位[1]：high 变为 high（改）
- K3 的思考档位设置.有效档位[2]：max 变为 max（改）
- K3 的思考档位设置.默认档位：high 变为 high（改）

##### Kimi Code · K3-256k（17 个字段）

- K3-256k 的模型 ID：k3-256k 变为 k3-256k（改）
- K3-256k 的创建时间戳：1761264000 变为 1761264001
- K3-256k 的创建时间：2025-10-24T00:00:00Z 变为 2025-10-24T00:00:00Z（改）
- K3-256k 的对象类型：model 变为 model（改）
- K3-256k（改） 的显示名：K3-256k 变为 K3-256k（改）
- K3-256k 的类型：model 变为 model（改）
- K3-256k 的上下文长度：262144 变为 262145
- K3-256k 的支持推理：是 变为 否
- K3-256k 的支持图片输入：是 变为 否
- K3-256k 的支持视频输入：否 变为 是
- K3-256k 的支持动态工具：是 变为 否
- K3-256k 的思考模式类型：only 变为 only（改）
- K3-256k 的思考档位设置.支持：是 变为 否
- K3-256k 的思考档位设置.有效档位[0]：low 变为 low（改）
- K3-256k 的思考档位设置.有效档位[1]：high 变为 high（改）
- K3-256k 的思考档位设置.有效档位[2]：max 变为 max（改）
- K3-256k 的思考档位设置.默认档位：high 变为 high（改）

### 三、Moonshot 官方模型列表

说明：created 字段每次请求都会漂移（接口侧列表生成时间），自 v1.5.1 起不参与对比，下表中的 created 一项仅作留档。

#### 按字段类型（每类一个例子）

1. **context_length**（4 处）

   示例（Moonshot 官方 · kimi-k2.6）：
   kimi-k2.6 的上下文长度：262144 变为 262145

2. **created**（4 处）

   示例（Moonshot 官方 · kimi-k2.6）：
   kimi-k2.6 的创建时间戳：1789135320 变为 1789135321

3. **id**（4 处）

   示例（Moonshot 官方 · kimi-k2.6）：
   新增模型：kimi-k2.6（改）（标识 kimi-k2.6（改））
   移除模型：kimi-k2.6（标识 kimi-k2.6）

4. **object**（4 处）

   示例（Moonshot 官方 · kimi-k2.6）：
   kimi-k2.6 的对象类型：model 变为 model（改）

5. **owned_by**（4 处）

   示例（Moonshot 官方 · kimi-k2.6）：
   kimi-k2.6 的归属方：moonshot 变为 moonshot（改）

6. **parent**（4 处）

   示例（Moonshot 官方 · kimi-k2.6）：
   kimi-k2.6 的父模型：（空） 变为 （改）

7. **permission[0].created**（4 处）

   示例（Moonshot 官方 · kimi-k2.6）：
   kimi-k2.6 的权限信息[0].创建时间戳：0 变为 1

8. **permission[0].group**（4 处）

   示例（Moonshot 官方 · kimi-k2.6）：
   kimi-k2.6 的权限信息[0].分组：moonshot 变为 moonshot（改）

9. **permission[0].id**（4 处）

   示例（Moonshot 官方 · kimi-k2.6）：
   kimi-k2.6 的权限信息[0].模型 ID：（空） 变为 （改）

10. **permission[0].is_blocking**（4 处）

   示例（Moonshot 官方 · kimi-k2.6）：
   kimi-k2.6 的权限信息[0].是否阻断：否 变为 是

11. **permission[0].object**（4 处）

   示例（Moonshot 官方 · kimi-k2.6）：
   kimi-k2.6 的权限信息[0].对象类型：（空） 变为 （改）

12. **permission[0].organization**（4 处）

   示例（Moonshot 官方 · kimi-k2.6）：
   kimi-k2.6 的权限信息[0].组织：moonshot 变为 moonshot（改）

13. **root**（4 处）

   示例（Moonshot 官方 · kimi-k2.6）：
   kimi-k2.6 的根模型：（空） 变为 （改）

14. **supports_image_in**（4 处）

   示例（Moonshot 官方 · kimi-k2.6）：
   kimi-k2.6 的支持图片输入：是 变为 否

15. **supports_reasoning**（4 处）

   示例（Moonshot 官方 · kimi-k2.6）：
   kimi-k2.6 的支持推理：是 变为 否

16. **supports_video_in**（4 处）

   示例（Moonshot 官方 · kimi-k2.6）：
   kimi-k2.6 的支持视频输入：是 变为 否

17. **reasoning_efforts.default_effort**（1 处）

   示例（Moonshot 官方 · kimi-k3）：
   kimi-k3 的推理档位设置.默认档位：max 变为 max（改）

18. **reasoning_efforts.support**（1 处）

   示例（Moonshot 官方 · kimi-k3）：
   kimi-k3 的推理档位设置.支持：是 变为 否

19. **reasoning_efforts.valid_efforts[0]**（1 处）

   示例（Moonshot 官方 · kimi-k3）：
   kimi-k3 的推理档位设置.有效档位[0]：low 变为 low（改）

20. **reasoning_efforts.valid_efforts[1]**（1 处）

   示例（Moonshot 官方 · kimi-k3）：
   kimi-k3 的推理档位设置.有效档位[1]：high 变为 high（改）

21. **reasoning_efforts.valid_efforts[2]**（1 处）

   示例（Moonshot 官方 · kimi-k3）：
   kimi-k3 的推理档位设置.有效档位[2]：max 变为 max（改）

22. **supports_dynamic_tools**（1 处）

   示例（Moonshot 官方 · kimi-k3）：
   kimi-k3 的支持动态工具：是 变为 否

23. **supports_thinking_type**（1 处）

   示例（Moonshot 官方 · kimi-k3）：
   kimi-k3 的思考模式类型：only 变为 only（改）

24. **think_efforts.default_effort**（1 处）

   示例（Moonshot 官方 · kimi-k3）：
   kimi-k3 的思考档位设置.默认档位：max 变为 max（改）

25. **think_efforts.support**（1 处）

   示例（Moonshot 官方 · kimi-k3）：
   kimi-k3 的思考档位设置.支持：是 变为 否

26. **think_efforts.valid_efforts[0]**（1 处）

   示例（Moonshot 官方 · kimi-k3）：
   kimi-k3 的思考档位设置.有效档位[0]：low 变为 low（改）

27. **think_efforts.valid_efforts[1]**（1 处）

   示例（Moonshot 官方 · kimi-k3）：
   kimi-k3 的思考档位设置.有效档位[1]：high 变为 high（改）

28. **think_efforts.valid_efforts[2]**（1 处）

   示例（Moonshot 官方 · kimi-k3）：
   kimi-k3 的思考档位设置.有效档位[2]：max 变为 max（改）

#### 全部实例（按模型分组）

##### Moonshot 官方 · kimi-k2.6（16 个字段）

- kimi-k2.6 的上下文长度：262144 变为 262145
- kimi-k2.6 的创建时间戳：1789135320 变为 1789135321
- 新增模型：kimi-k2.6（改）（标识 kimi-k2.6（改））
- 移除模型：kimi-k2.6（标识 kimi-k2.6）
- kimi-k2.6 的对象类型：model 变为 model（改）
- kimi-k2.6 的归属方：moonshot 变为 moonshot（改）
- kimi-k2.6 的父模型：（空） 变为 （改）
- kimi-k2.6 的权限信息[0].创建时间戳：0 变为 1
- kimi-k2.6 的权限信息[0].分组：moonshot 变为 moonshot（改）
- kimi-k2.6 的权限信息[0].模型 ID：（空） 变为 （改）
- kimi-k2.6 的权限信息[0].是否阻断：否 变为 是
- kimi-k2.6 的权限信息[0].对象类型：（空） 变为 （改）
- kimi-k2.6 的权限信息[0].组织：moonshot 变为 moonshot（改）
- kimi-k2.6 的根模型：（空） 变为 （改）
- kimi-k2.6 的支持图片输入：是 变为 否
- kimi-k2.6 的支持推理：是 变为 否
- kimi-k2.6 的支持视频输入：是 变为 否

##### Moonshot 官方 · kimi-k3（28 个字段）

- kimi-k3 的上下文长度：1048576 变为 1048577
- kimi-k3 的创建时间戳：1789135320 变为 1789135321
- 新增模型：kimi-k3（改）（标识 kimi-k3（改））
- 移除模型：kimi-k3（标识 kimi-k3）
- kimi-k3 的对象类型：model 变为 model（改）
- kimi-k3 的归属方：moonshot 变为 moonshot（改）
- kimi-k3 的父模型：（空） 变为 （改）
- kimi-k3 的权限信息[0].创建时间戳：0 变为 1
- kimi-k3 的权限信息[0].分组：moonshot 变为 moonshot（改）
- kimi-k3 的权限信息[0].模型 ID：（空） 变为 （改）
- kimi-k3 的权限信息[0].是否阻断：否 变为 是
- kimi-k3 的权限信息[0].对象类型：（空） 变为 （改）
- kimi-k3 的权限信息[0].组织：moonshot 变为 moonshot（改）
- kimi-k3 的推理档位设置.默认档位：max 变为 max（改）
- kimi-k3 的推理档位设置.支持：是 变为 否
- kimi-k3 的推理档位设置.有效档位[0]：low 变为 low（改）
- kimi-k3 的推理档位设置.有效档位[1]：high 变为 high（改）
- kimi-k3 的推理档位设置.有效档位[2]：max 变为 max（改）
- kimi-k3 的根模型：（空） 变为 （改）
- kimi-k3 的支持动态工具：是 变为 否
- kimi-k3 的支持图片输入：是 变为 否
- kimi-k3 的支持推理：是 变为 否
- kimi-k3 的思考模式类型：only 变为 only（改）
- kimi-k3 的支持视频输入：是 变为 否
- kimi-k3 的思考档位设置.默认档位：max 变为 max（改）
- kimi-k3 的思考档位设置.支持：是 变为 否
- kimi-k3 的思考档位设置.有效档位[0]：low 变为 low（改）
- kimi-k3 的思考档位设置.有效档位[1]：high 变为 high（改）
- kimi-k3 的思考档位设置.有效档位[2]：max 变为 max（改）

##### Moonshot 官方 · kimi-k2.7-code（16 个字段）

- kimi-k2.7-code 的上下文长度：262144 变为 262145
- kimi-k2.7-code 的创建时间戳：1789135320 变为 1789135321
- 新增模型：kimi-k2.7-code（改）（标识 kimi-k2.7-code（改））
- 移除模型：kimi-k2.7-code（标识 kimi-k2.7-code）
- kimi-k2.7-code 的对象类型：model 变为 model（改）
- kimi-k2.7-code 的归属方：moonshot 变为 moonshot（改）
- kimi-k2.7-code 的父模型：（空） 变为 （改）
- kimi-k2.7-code 的权限信息[0].创建时间戳：0 变为 1
- kimi-k2.7-code 的权限信息[0].分组：moonshot 变为 moonshot（改）
- kimi-k2.7-code 的权限信息[0].模型 ID：（空） 变为 （改）
- kimi-k2.7-code 的权限信息[0].是否阻断：否 变为 是
- kimi-k2.7-code 的权限信息[0].对象类型：（空） 变为 （改）
- kimi-k2.7-code 的权限信息[0].组织：moonshot 变为 moonshot（改）
- kimi-k2.7-code 的根模型：（空） 变为 （改）
- kimi-k2.7-code 的支持图片输入：是 变为 否
- kimi-k2.7-code 的支持推理：是 变为 否
- kimi-k2.7-code 的支持视频输入：是 变为 否

##### Moonshot 官方 · kimi-k2.7-code-highspeed（16 个字段）

- kimi-k2.7-code-highspeed 的上下文长度：262144 变为 262145
- kimi-k2.7-code-highspeed 的创建时间戳：1789135320 变为 1789135321
- 新增模型：kimi-k2.7-code-highspeed（改）（标识 kimi-k2.7-code-highspeed（改））
- 移除模型：kimi-k2.7-code-highspeed（标识 kimi-k2.7-code-highspeed）
- kimi-k2.7-code-highspeed 的对象类型：model 变为 model（改）
- kimi-k2.7-code-highspeed 的归属方：moonshot 变为 moonshot（改）
- kimi-k2.7-code-highspeed 的父模型：（空） 变为 （改）
- kimi-k2.7-code-highspeed 的权限信息[0].创建时间戳：0 变为 1
- kimi-k2.7-code-highspeed 的权限信息[0].分组：moonshot 变为 moonshot（改）
- kimi-k2.7-code-highspeed 的权限信息[0].模型 ID：（空） 变为 （改）
- kimi-k2.7-code-highspeed 的权限信息[0].是否阻断：否 变为 是
- kimi-k2.7-code-highspeed 的权限信息[0].对象类型：（空） 变为 （改）
- kimi-k2.7-code-highspeed 的权限信息[0].组织：moonshot 变为 moonshot（改）
- kimi-k2.7-code-highspeed 的根模型：（空） 变为 （改）
- kimi-k2.7-code-highspeed 的支持图片输入：是 变为 否
- kimi-k2.7-code-highspeed 的支持推理：是 变为 否
- kimi-k2.7-code-highspeed 的支持视频输入：是 变为 否

### 四、DeepSeek 官方模型列表

说明：`system_fingerprint`（后端指纹）不在 `/models` 里，是插件按单独间隔向每个模型发一条极小的聊天请求、从回复里读出后写进模型对象的，之后和其他字段一起参与对比。Kimi Code 与 Moonshot 官方的每个模型同样多出这一项，提示样式与下面的 DeepSeek 示例一致（例如：`k3 的后端指纹：fpv0_57ea5778 变为 fpv0_57ea5778（改）`）。

#### 按字段类型（每类一个例子）

1. **api_capabilities.anthropic_messages.system_prompt_update**（2 处）

   示例（DeepSeek 官方 · deepseek-flash）：
   deepseek-flash 的 API 能力.Anthropic 协议.system 提示更新方式：in-history 变为 in-history（改）

2. **context_window**（2 处）

   示例（DeepSeek 官方 · deepseek-flash）：
   deepseek-flash 的上下文窗口：1048576 变为 1048577

3. **effort.default_level**（2 处）

   示例（DeepSeek 官方 · deepseek-flash）：
   deepseek-flash 的档位.默认档位：high 变为 high（改）

4. **effort.supported_levels[0]**（2 处）

   示例（DeepSeek 官方 · deepseek-flash）：
   deepseek-flash 的档位.支持档位[0]：low 变为 low（改）

5. **effort.supported_levels[1]**（2 处）

   示例（DeepSeek 官方 · deepseek-flash）：
   deepseek-flash 的档位.支持档位[1]：high 变为 high（改）

6. **effort.supported_levels[2]**（2 处）

   示例（DeepSeek 官方 · deepseek-flash）：
   deepseek-flash 的档位.支持档位[2]：max 变为 max（改）

7. **id**（2 处）

   示例（DeepSeek 官方 · deepseek-flash）：
   新增模型：deepseek-flash（改）（标识 deepseek-flash（改））
   移除模型：deepseek-flash（标识 deepseek-flash）

8. **input_modalities[0]**（2 处）

   示例（DeepSeek 官方 · deepseek-flash）：
   deepseek-flash 的输入类型[0]：text 变为 text（改）

9. **input_modalities[1]**（1 处）

   示例（DeepSeek 官方 · deepseek-flash）：
   deepseek-flash 的输入类型[1]：image 变为 image（改）

10. **max_output_tokens**（2 处）

   示例（DeepSeek 官方 · deepseek-flash）：
   deepseek-flash 的最大输出：393216 变为 393217

11. **name**（2 处）

   示例（DeepSeek 官方 · deepseek-flash）：
   deepseek-flash 的名称：DeepSeek-V4.1-Flash 变为 DeepSeek-V4.1-Flash（改）

12. **object**（2 处）

   示例（DeepSeek 官方 · deepseek-flash）：
   deepseek-flash 的对象类型：model 变为 model（改）

13. **output_modalities[0]**（2 处）

   示例（DeepSeek 官方 · deepseek-flash）：
   deepseek-flash 的输出类型[0]：text 变为 text（改）

14. **owned_by**（2 处）

   示例（DeepSeek 官方 · deepseek-flash）：
   deepseek-flash 的归属方：deepseek 变为 deepseek（改）

15. **system_fingerprint**（2 处）

   示例（DeepSeek 官方 · deepseek-flash）：
   deepseek-flash 的后端指纹：e72d3c7f663d3e1383b00b3884cb4501 变为 e72d3c7f663d3e1383b00b3884cb4501（改）

#### 全部实例（按模型分组）

##### DeepSeek 官方 · deepseek-flash（15 个字段）

- deepseek-flash 的 API 能力.Anthropic 协议.system 提示更新方式：in-history 变为 in-history（改）
- deepseek-flash 的上下文窗口：1048576 变为 1048577
- deepseek-flash 的档位.默认档位：high 变为 high（改）
- deepseek-flash 的档位.支持档位[0]：low 变为 low（改）
- deepseek-flash 的档位.支持档位[1]：high 变为 high（改）
- deepseek-flash 的档位.支持档位[2]：max 变为 max（改）
- 新增模型：deepseek-flash（改）（标识 deepseek-flash（改））
- 移除模型：deepseek-flash（标识 deepseek-flash）
- deepseek-flash 的输入类型[0]：text 变为 text（改）
- deepseek-flash 的输入类型[1]：image 变为 image（改）
- deepseek-flash 的最大输出：393216 变为 393217
- deepseek-flash 的名称：DeepSeek-V4.1-Flash 变为 DeepSeek-V4.1-Flash（改）
- deepseek-flash 的对象类型：model 变为 model（改）
- deepseek-flash 的输出类型[0]：text 变为 text（改）
- deepseek-flash 的归属方：deepseek 变为 deepseek（改）
- deepseek-flash 的后端指纹：e72d3c7f663d3e1383b00b3884cb4501 变为 e72d3c7f663d3e1383b00b3884cb4501（改）

##### DeepSeek 官方 · deepseek-v4-pro（14 个字段）

- deepseek-v4-pro 的 API 能力.Anthropic 协议.system 提示更新方式：leading-only 变为 leading-only（改）
- deepseek-v4-pro 的上下文窗口：1048576 变为 1048577
- deepseek-v4-pro 的档位.默认档位：high 变为 high（改）
- deepseek-v4-pro 的档位.支持档位[0]：low 变为 low（改）
- deepseek-v4-pro 的档位.支持档位[1]：high 变为 high（改）
- deepseek-v4-pro 的档位.支持档位[2]：max 变为 max（改）
- 新增模型：deepseek-v4-pro（改）（标识 deepseek-v4-pro（改））
- 移除模型：deepseek-v4-pro（标识 deepseek-v4-pro）
- deepseek-v4-pro 的输入类型[0]：text 变为 text（改）
- deepseek-v4-pro 的最大输出：393216 变为 393217
- deepseek-v4-pro 的名称：DeepSeek-V4-Pro 变为 DeepSeek-V4-Pro（改）
- deepseek-v4-pro 的对象类型：model 变为 model（改）
- deepseek-v4-pro 的输出类型[0]：text 变为 text（改）
- deepseek-v4-pro 的归属方：deepseek 变为 deepseek（改）
- deepseek-v4-pro 的后端指纹：a307abda487cd1b463329ccb945ce396 变为 a307abda487cd1b463329ccb945ce396（改）

## 备注

监测对象为服务器所在网络视角下的 kimi.com 接口，以及所配置的 Kimi Code、Moonshot、DeepSeek 官方接口（Key 自备）。

各官方接口只能看到所配置 Key 可见的模型列表。

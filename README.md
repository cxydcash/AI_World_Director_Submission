# AI World Director
## AI 驱动的开放世界玩法探索

> 当前版本是一个探索：**AI 如何作为世界导演，使开放世界能够根据玩家行为、NPC 状态和世界事件持续产生新的故事与探索可能性。**

项目经过多阶段迭代，目前已完成 AI World Director 核心机制验证。当前仓库定位为 **AI World Director 的核心机制验证型 Prototype**，不是已经完成的商业化开放世界游戏。

---

## 评审快速体验（无需 API Key）

### 方式一：离线展示 / Offline Showcase

```bash
python offline_showcase.py
```

说明：

- 无需 API Key
- 无需联网
- 无需配置 `.env`
- 展示 NPC 自主行动、WorldState 演化、剧情偏离、StoryEvent、Goal 和探索线索

`Offline Showcase` 使用预先设计的确定性 Director 输出，但 WorldState、StoryTimeline、GoalManager、Event DSL 和 NPC Planner 仍然由项目中的现有代码真实执行。它不是真实 LLM 现场推理。

### 方式二：完整 LLM Director

完整模式需要：

- DeepSeek API Key
- `.env`
- 网络连接

不要把真实 Key 放进项目。

| 模式 | API Key | 网络 | 作用 |
|---|---|---|---|
| Offline Showcase | 不需要 | 不需要 | 评审快速体验核心机制 |
| LLM Director | 需要 | 需要 | 使用 DeepSeek 根据世界状态生成 StoryEvent / Goal / Mystery |

Offline Showcase 不能被描述为真实 LLM 推理；它只用于稳定展示 Director 输出进入现有世界链路后的效果。

---

## 1. 核心理念

**LLM 负责“想什么、发生什么”，程序负责“怎么真正发生”。**

AI 不直接修改世界，而是读取受控的世界上下文并生成结构化提案；程序负责条件判断、Schema 校验、Action/Event 执行和 WorldState 写入。

```text
WorldState
    -> AI World Director
    -> StoryEvent / GoalChange
    -> StoryTimeline / GoalManager / Event DSL
    -> Validator / Executor
    -> WorldState
    -> NPC / Player Experience
    -> 新的 WorldState
```

这条链路的核心约束是：

- LLM 只能生成结构化 Intent、GoalChange、Event DSL 或 StoryEvent 提案。
- 所有世界变化必须经过现有 Validator / Executor。
- NPC Goal 由 AI Director 决定，GoalManager 负责统一写入，NPC Planner 只负责执行。
- StoryTimeline 保存未来可执行的剧情事件，事件是否触发取决于真实 WorldState，而不是预先假定一定发生。

---

## 2. 已实现能力

| 能力 | 主要代码 | 当前实现 |
|---|---|---|
| 连续世界状态 | `world.py`、`entities.py`、`world_objects.py` | 维护时间、玩家、NPC、物品、证据、历史、环境、任务和世界阶段。 |
| Intent Layer | `intent.py`、`director_input.py`、`intent_legacy.py` | 将对话、想法和自由输入整理为可供后续系统处理的信息。 |
| Action Layer | `action_layer.py`、`action.py`、`action_time.py` | 提供 Action 构建、上下文校验、执行和时间消耗。 |
| AI World Director | `director.py`、`open_world.py`、`story_director.py` | 根据真实世界状态生成 Event DSL、StoryEvent 和 GoalChange 提案。 |
| Event DSL | `event.py`、`effect_normalizer.py` | 通过受限效果类型执行世界变化，例如移动 NPC、创建 NPC、添加证据、修改记忆/信念/关系/目标。 |
| Story Timeline | `story_timeline.py` | 支持 StoryEvent 的 pending、deferred、blocked、triggered、completed、cancelled、modified 等状态及条件检查。 |
| Goal 生命周期 | `goal_manager.py`、`director_input.py` | AI Director 决定新增、移除或调整 Goal；GoalManager 通过 Event DSL 写入 WorldState。 |
| NPC 自主行为 | `npc_planner.py`、`npc_movement_intent.py`、`open_world.py` | NPC 根据已有 Goal、位置、可见信息、记忆和信念执行移动、调查、跟随、到达地点、交谈等行为。 |
| Evidence / Memory / Belief | `evidence.py`、`entities.py`、`conversation_memory.py` | NPC 可在局部范围内通过搜索/调查发现 Evidence，并形成 Memory、Belief 和后续 Goal。 |
| 剧情偏离检测 | `plot_deviation.py`、`night_director.py` | 检测 NPC 死亡/失踪、Goal 失败或被抢占、物品缺失、Timeline 阻塞或延期、位置偏离等情况。 |
| 每日夜间编排 | `night_director.py` | 22:00-24:00 进入 Night Director，暂停普通玩家行动、NPC Planner 和普通 StoryEvent 推进，基于当天状态重新规划下一天。 |
| 自然偏航与重编 | `director.py`、`night_director.py`、`story_timeline.py` | Director 可以根据偏离选择保持、延迟、修改、取消、替换或分支剧情，也可以不新增剧情。 |
| 玩家视角展示 | `visualization/app.py`、`visualization/app.js`、`visualization/player_view.py` | 通过只读快照展示地图、地点、NPC、线索、玩家行为和叙事信息；展示层不直接修改 WorldState。 |
| 离线 Demo 与测试 | `offline_showcase.py`、`demo_step12_8.py`、`final_demo.py`、`test_*.py` | 提供无需 API Key 的确定性离线展示，以及覆盖核心链路的自动化测试。 |

### 已验证的闭环方向

- 玩家对话和玩家行为可以作为 Director Input 影响 NPC Goal。
- NPC Goal 经由 GoalManager 进入统一生命周期，并由 NPC Planner 执行。
- StoryEvent 可以进入 StoryTimeline，并在条件满足时通过 Event DSL 改变 WorldState。
- Evidence 可以发现，并进一步影响 NPC 的 Memory、Belief 和 Goal。
- 玩家不操作时，世界可以继续通过时间、Timeline 和 NPC autonomy 演化。
- 当剧情条件被玩家改变时，系统允许原剧情自然失效，并把偏离信息交给 Director 重新编排。

---

## 3. 当前限制

1. 玩家 Thought -> Action 的完整映射仍在开发中。
2. AI 生成的 Evidence / Mystery 到玩家可执行 Action 的体验层仍需加强。
3. AI 生成内容的长期数量控制仍需进一步验证。
4. 当前重点是核心机制 Prototype，而非完整商业游戏。

当前版本已经完成玩家语言/想法的意图层建模，但玩家想法到具体可执行 Action 的完整映射仍在开发中。后续将进一步将 Thought -> Intent -> Action，与现有 Action Validator / Executor / WorldState 链路完整打通，使玩家的自然语言想法能够对应到更加具体、可执行的世界操作。

当前版本已经实现的核心验证方向包括：AI 是否能够理解世界状态、生成新的 StoryEvent、让 NPC 自主行动、让世界持续演化、让玩家行为造成剧情偏离，以及根据偏离生成新的故事方向。当前版本没有完整商业化游戏、完整美术资源、完整战斗系统、完整开放世界地图或完整玩家 Action 自由度。

---

## 4. 后续计划

```text
Player Thought
    -> Intent
    -> Action
    -> Validator
    -> Executor
    -> WorldState
```

```text
World Truth
    -> Evidence
    -> Discovery
    -> Player Knowledge
    -> Player Decision
```

最终希望形成完整闭环：

```text
Player
    -> Action / Thought
    -> WorldState
    -> NPC / AI Director
    -> StoryEvent
    -> World Evolution
    -> Player Discovery
    -> Player Decision
    -> ...
```

---

## 5. 项目结构

```text
AI_World_Director_Submission/
├─ world.py                 # WorldState、时间、玩家/NPC/物品/证据/历史等世界数据
├─ entities.py              # Player、NPC、Item 及记忆/信念/关系/Goal 数据模型
├─ intent.py                # 玩家对话、想法和自由输入的 Intent 层
├─ action_layer.py          # Action 构建、Validator、Executor
├─ action.py                # 基础世界动作及玩家动作入口
├─ npc_planner.py           # NPC Planner：读取 Goal 并产生 NPC Action
├─ goal_manager.py          # Director GoalChange 到 Event DSL 的唯一写入入口
├─ evidence.py              # Evidence 创建、局部发现、Memory/Belief/Goal 联动
├─ event.py                 # Event DSL、Validator 和 Executor
├─ story_timeline.py         # StoryEvent 生命周期、条件检查和执行
├─ story_director.py         # StoryEvent 提案和校验
├─ director.py               # LLM/离线 Director、上下文构建和 Event 提案
├─ night_director.py         # 22:00-24:00 每日夜间编排
├─ plot_deviation.py         # 只读剧情偏离检测
├─ open_world.py             # OpenWorldEngine：世界 tick、输入、NPC、Timeline、Phase 编排
├─ persistence.py            # JSON WorldState save/load
├─ visualization/
│  ├─ app.py                 # 零第三方依赖 HTTP 服务和 API 路由
│  ├─ app.js                 # Canvas 地图与面板渲染
│  ├─ player_view.py         # 玩家视角、停留位置、行为和叙事聚合
│  └─ ...
├─ demo_flow.py              # 对话/开放世界 Demo
├─ offline_showcase.py       # 评审无需 API Key 的 Offline Showcase
├─ demo_step12_8.py          # 确定性两路线离线 Demo
├─ final_demo.py             # 脚本化完整链路 Demo
└─ test_*.py                 # 核心机制、运行时和回归测试
```

---

## 6. 运行方式

### 环境

- 推荐 Python 3.11。
- 项目核心运行不依赖第三方 Python 包，使用标准库。
- 测试基于 `unittest`；如本机额外安装了 pytest，也可使用 pytest 命令。

### API Key 配置

本项目使用 DeepSeek API 进行 AI World Director 的故事事件生成。API Key 通过环境变量配置，作品提交文件中不包含真实 API Key。

复制配置模板并填写自己的值：

```powershell
Copy-Item .env.example .env
# 编辑 .env，填入自己的 API Key、DeepSeek API Base URL 和模型名
```

也可以直接设置当前终端的环境变量：

```powershell
$env:LLM_API_KEY="your_api_key_here"
$env:LLM_BASE_URL="your_deepseek_api_base_url_here"
$env:LLM_MODEL="your_deepseek_model_name_here"
$env:DIRECTOR_MODE="llm"
```

不要把真实 `.env` 或真实 Key 提交到作品包。未配置 API Key 时，系统仍可运行离线/确定性路径，但不会执行真实 DeepSeek 生成。

### 启动 Web Demo

在项目根目录运行：

```powershell
python visualization/app.py --host 127.0.0.1 --port 8000
```

然后打开：

```text
http://127.0.0.1:8000
```

### 运行确定性离线 Demo

```powershell
python demo_step12_8.py
```

该 Demo 使用真实 `OpenWorldEngine`，但使用确定性 Night Director provider，因此不要求网络或 API Key。

### 运行脚本化 Demo

```powershell
python final_demo.py --baseline
```

使用 LLM Director 运行时可以省略 `--baseline`：

```powershell
python final_demo.py
```

### 运行测试

```powershell
python -m unittest discover -v
```

---

## 7. 安全与提交说明

- 作品提交副本不包含真实 `.env`、API Key、token 或密码。
- `.env.example` 只包含占位符，实际运行前请在本地创建未提交的 `.env`。
- `game_state.json` 是持久化模块使用的世界状态示例/运行状态文件，不包含 API 凭据。
- 展示层只读取 WorldState 快照，不直接修改后端真实世界状态。




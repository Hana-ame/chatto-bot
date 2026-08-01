# ChattoBot

<p align="center">
  <img src=".github/social-preview.png" alt="ChattoBot" width="640">
</p>

基于 [Chatto](https://chatto.run) 的 Python bot 框架：装饰器命令、discord.py 风格 cog、带自动重连的实时事件流、带类型提示的参数解析。

## 快速开始

```bash
pip install -e .
```

设置 bot 凭据（启动时自动换取 bearer token）：

```bash
export CHATTO_EMAIL="bot@example.com"
export CHATTO_PASSWORD="..."
```

如果你已有 token，直接设置 `CHATTO_TOKEN` 即可。

写一个 bot：

```python
from chatto_bot import Bot, Context

bot = Bot(
    instance="https://chat.chatto.run",
    prefix="!",
)

@bot.command(desc="检查 bot 是否存活")
async def ping(ctx: Context):
    await ctx.reply("Pong!")

bot.run()
```

## 功能特性

- 装饰器命令，参数按类型提示自动解析
- 命令通过前缀（`!ping`）或 @提及 bot（`@BotName ping`）触发
- 任意事件类型的事件处理器（`message_posted`、`reaction_added`、…）
- 用 cog 把命令和处理器分组为可加载的扩展
- 中间件链（日志、忽略自身消息、权限等）
- 基于 protobuf WebSocket 的实时事件流，自动重连 + 退避
- 断线重连会重放最多一小时错过的消息
- Bearer token 认证（邮箱/密码或 `CHATTO_TOKEN`），cookie session 兜底
- SIGINT/SIGTERM/SIGHUP 优雅退出，状态持久化

## 命令

```python
@bot.command(desc="掷骰子", aliases=["r"])
async def roll(ctx: Context, sides: int = 6):
    """参数按类型提示解析。"""
    await ctx.reply(f"Rolled: {random.randint(1, sides)}")
```

## 事件

```python
@bot.on_event("message_posted")
async def on_message(ctx: Context):
    if ctx.body and "hello" in ctx.body.lower():
        await ctx.react("wave")  # 表情用 emoji shortcode，不是 unicode
```

## Cogs

```python
from chatto_bot import Cog, command, on_event

class Greeter(Cog):
    @command(desc="打个招呼")
    async def hello(self, ctx: Context):
        await ctx.reply(f"Hello, {ctx.actor.display_name}!")

    @on_event("user_joined_room")
    async def on_join(self, ctx: Context):
        await ctx.reply("Welcome!")

    async def cog_load(self):
        print("Greeter loaded")

async def setup(bot):
    await bot.add_cog(Greeter(bot))
```

动态加载扩展：

```python
await bot.load_extension("plugins.greeter")
await bot.reload_extension("plugins.greeter")  # 热重载
```

## 中间件

```python
@bot.middleware
async def log_commands(ctx, next):
    print(f"{ctx.actor.login}: {ctx.body}")
    await next()
```

## 配置

三个来源，优先级从高到低：`Bot(...)` 显式参数、环境变量（以及 `.env`）、YAML 文件。

环境变量：

| 变量 | 说明 |
|----------|-------------|
| `CHATTO_TOKEN` | Bearer token。设置了就跳过登录。 |
| `CHATTO_EMAIL` / `CHATTO_PASSWORD` | 登录凭据。启动时自动换取 bearer token。 |
| `CHATTO_SESSION` | Session cookie，认证兜底。 |
| `CHATTO_INSTANCE` | 实例 URL（默认 `https://dev.chatto.run`）。 |
| `CHATTO_PREFIX` | 命令前缀（默认 `!`）。 |
| `CHATTO_ROOMS` | 逗号分隔的房间 ID 白名单。留空 = 所有房间。 |
| `CHATTO_ADMINS` | 逗号分隔的登录名，允许执行 `admin=True` 命令。 |
| `CHATTO_DMS` | `false` / `0` / `no` 禁用私聊处理。默认启用。 |

YAML 配置（通过 `Bot(config_path="chatto-bot.yaml")` 传入）：

```yaml
instance: https://chat.chatto.run
prefix: "!"
dms: true

admins:
  - alice
  - bob

extensions:
  - plugins.admin
  - plugins.remind
```

密钥（`token`、`session`、`email`、`password`）不要写进 YAML，用 `.env` 或环境变量。

## AI bot

`plugins/ai.py` 扩展把 bot 变成 LLM 驱动的助手。它监听 `message_posted`，
当消息以 AI 前缀（默认 `!ai`）开头或 @提及 bot（`@<login> ...`）时回复。

像加载其他扩展一样加载它：

```yaml
extensions:
  - plugins.ai
```

通过环境变量配置 OpenAI 兼容端点：

| 变量 | 说明 |
|----------|-------------|
| `OPENAI_BASE_URL` | 完整的 OpenAI 兼容 `.../chat/completions` 端点 URL。兼容任意 OpenAI 兼容端点（OpenAI、DeepSeek、本地 Ollama 等）。 |
| `OPENAI_API_KEY` | 端点的 API key。 |
| `OPENAI_MODEL` | 模型名（默认 `gpt-4o-mini`）。 |
| `OPENAI_SYSTEM_PROMPT` | 可选的系统提示词。 |
| `AI_PREFIX` | 触发前缀（默认 `!ai`）。 |

`.env` 示例：

```
CHATTO_INSTANCE=https://chat.chatto.run
CHATTO_EMAIL=ai-bot@example.com
CHATTO_PASSWORD=...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
```

## 在 Windows 10 上运行

AI bot 就是一个纯 Python 程序——在 Windows 10 上直接用 `py` 启动器运行
（无需编译任何东西）。

### 0. 前置条件

- **Git** — [git-scm.com](https://git-scm.com/download/win)
- **Python 3.11+** — [python.org](https://www.python.org/downloads/)。
  安装时勾选 **Add Python to PATH**。
- **一个 Chatto bot 账号**。如果你的 Chatto 实例开启了邮箱验证但又收不到邮件
  （没有配 SMTP），可以让实例管理员用 operator CLI 直接创建 bot 用户：

  ```
  chatto operator user create \
      --login ai-bot \
      --display-name "AI Bot" \
      --verified-email ai-bot@example.com \
      --password '<password>' \
      --role owner
  ```

### 1. 克隆仓库

```bat
git clone git@github.com:Hana-ame/chatto-bot.git
cd chatto-bot
```

（也可以先在 GitHub 上 fork 一份，再 clone 自己的 fork。）

### 2. 安装

`run_ai_bot.py` 里有 `from chatto_bot import Bot`，所以框架包及其依赖
（`httpx`、`websockets`、`connectrpc`、`pyyaml`）需要先安装一次。
`pip install -e .` 安装的是**当前目录**——也就是你刚克隆的这份代码，
用可编辑模式（本地改动即时生效，无需重装）：

```bat
py -m pip install -e .
```

### 3. 配置

在 `run_ai_bot.py` 旁边创建 `.env`，填入凭据和 LLM 端点：

```
CHATTO_INSTANCE=https://chatto.moonchan.xyz
CHATTO_EMAIL=ai-bot
CHATTO_PASSWORD=...
OPENAI_BASE_URL=https://your-host/v1/chat/completions
OPENAI_API_KEY=...
OPENAI_MODEL=your-model
```

### 4. 运行

```bat
py run_ai_bot.py
```

就这么简单。bot 会登录、加入所有可见房间，并回复
`!ai <prompt>` 或 `@ai-bot <prompt>`。

想在 Windows 上后台常驻：开一个 PowerShell 窗口别关它，或者注册为登录时
启动的计划任务。

## License

[AGPL-3.0-or-later](LICENSE)

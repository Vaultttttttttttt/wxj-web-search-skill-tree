# InnoSpark Web Search — 环境配置指南

> 本文档面向**首次配置**的用户，帮助你从零开始运行整个技能树。

---

## 目录

1. [系统要求](#一系统要求)
2. [快速上手（5分钟）](#二快速上手5分钟)
3. [API Key 申请指南](#三api-key-申请指南)
4. [各模块安装](#四各模块安装)
5. [Claude Code 接入](#五claude-code-接入)
6. [验证安装](#六验证安装)
7. [常见问题](#七常见问题)

---

## 一、系统要求

| 依赖 | 最低版本 | 检查命令 |
|------|---------|---------|
| Python | 3.8+ | `python3 --version` |
| pip | 任意 | `pip3 --version` |
| Node.js | 16+ | `node --version` |
| npm | 7+ | `npm --version` |
| curl | 任意 | `curl --version` |
| jq | 任意 | `jq --version` |
| Git | 任意 | `git --version` |
| Chrome 浏览器 | 任意新版 | — |

**macOS 一键安装所有系统依赖：**
```bash
brew install python node git jq curl
```

**Windows / Linux：** 请从各官网下载安装。

> **推荐使用 conda 管理 Python 环境**，避免与系统 Python 冲突：
> ```bash
> conda create -n innospark python=3.11
> conda activate innospark
> pip install -r requirements.txt
> ```

---

## 二、快速上手（5分钟）

### Step 1：克隆/复制技能树

将 `web-search-innospark-tree/` 整个目录放到你的 `~/.claude/skills/` 下：

```bash
# 如果你拿到的是 zip 包
unzip web-search-innospark-tree.zip -d ~/.claude/skills/

# 如果你拿到的是文件夹，直接复制
cp -r web-search-innospark-tree ~/.claude/skills/
```

### Step 2：配置环境变量

```bash
# 进入技能树目录
cd ~/.claude/skills/web-search-innospark-tree

# 复制模板
cp .env.example .env

# 编辑 .env，填入你的 API Key（见下方申请指南）
nano .env   # 或用任意文本编辑器打开
```

### Step 3：安装 Python 依赖

```bash
# 【推荐】用 conda 隔离环境（避免与系统 Python 冲突）
conda create -n innospark python=3.11 -y
conda activate innospark

# 一键安装所有 Python 依赖（含核心 + 可选）
pip install -r ~/.claude/skills/web-search-innospark-tree/requirements.txt

# 单独安装 yt-dlp（视频字幕，last30days 核心工具）
pip install yt-dlp

# 可选：HuggingFace Papers / Ben's Bites 需要 Playwright
pip install playwright && playwright install chromium
```

**requirements.txt 包含的主要包：**

| 包 | 用途 |
|----|------|
| `requests` | 所有模块 HTTP 请求 |
| `python-dotenv` | 加载 .env 环境变量 |
| `beautifulsoup4` + `lxml` | HTML 解析 |
| `feedparser` | RSS 解析 |
| `httpx[socks]` | Intel Briefing 异步请求 |
| `google-genai` | Gemini 中文翻译（可选） |
| `loguru` | Union Search 日志 |
| `yt-dlp` | YouTube/B站字幕 |

### Step 4：安装系统工具

```bash
# OpenCLI-RS（平台内容获取，推荐版本，已提供二进制）
mkdir -p ~/bin
# 将 opencli-rs 二进制复制到 ~/bin/
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.zshrc

# OpenCLI JS 版（备用）
npm install -g @jackwener/opencli

# Twitter/X 访问
npm install -g @steipete/bird

# GitHub CLI
brew install gh && gh auth login

# UltimateSearch 依赖的系统工具
brew install jq curl
```

### Step 4：打开 Claude Code，验证

```bash
claude
```
在 Claude Code 中输入：`/web-search-innospark-tree`，看到技能树路由说明即配置成功。

---

## 三、API Key 申请指南

> **优先级说明**：✅ 必需 | ⭐ 推荐 | 💡 可选

### ✅ GITHUB_TOKEN（必需）

- **用途**：GitHub Trending 数据，Intel Briefing 核心数据源
- **申请**：https://github.com/settings/tokens → Generate new token (classic)
- **权限**：只勾选 `public_repo`（只读公开仓库即可）
- **费用**：完全免费

```
GITHUB_TOKEN=your_github_token_here
```

### ⭐ XAI_API_KEY（推荐）

- **用途**：Grok AI 搜索、Twitter/X 舆情分析、Alpha 雷达、营收分析
- **申请**：https://console.x.ai/
- **费用**：每月 $25 免费额度（对学生/个人足够）

```
XAI_API_KEY=your_xai_api_key_here
```

> 同一个 Key 同时填入 `GROK_API_KEY`（UltimateSearch 模块使用相同 Key）

### ⭐ TAVILY_API_KEY（推荐）

- **用途**：结构化网页搜索，UltimateSearch 核心引擎之一
- **申请**：https://app.tavily.com/
- **费用**：免费 1000 次/月

```
TAVILY_API_KEY=tvly-xxxxxxxxxxxxxxxxxxxx
```

### ⭐ SCRAPECREATORS_API_KEY（推荐）

- **用途**：Reddit / TikTok / Instagram 内容搜索（last30days 模块核心）
- **申请**：https://scrapecreators.com/
- **费用**：前 100 次免费，之后按量付费

```
SCRAPECREATORS_API_KEY=your_key_here
```

### 💡 TIKHUB_TOKEN（可选）

- **用途**：小红书 / 抖音内容搜索（Union Search 模块）
- **申请**：https://tikhub.io/
- **费用**：有免费额度

```
TIKHUB_TOKEN=your_tikhub_token
```

### 💡 其他可选 Key

| 变量名 | 用途 | 申请地址 |
|--------|------|---------|
| `PRODUCTHUNT_TOKEN` | Product Hunt 新品数据 | https://www.producthunt.com/v2/oauth/applications |
| `GEMINI_API_KEY` | 中文翻译（ArXiv 论文等） | https://aistudio.google.com/apikey |
| `GOOGLE_API_KEY` + `GOOGLE_SEARCH_ENGINE_ID` | Google 自定义搜索 | https://console.cloud.google.com/ |
| `YOUTUBE_API_KEY` | YouTube 视频搜索 | https://console.cloud.google.com/ |
| `BRAVE_API_KEY` | Brave 搜索补充 | https://api.search.brave.com/ |
| `JINA_API_KEY` | Jina 语义搜索 | https://jina.ai/ |
| `FIRECRAWL_API_KEY` | 网页抓取备用方案 | https://www.firecrawl.dev/ |
| `OPENAI_API_KEY` | 备用 LLM | https://platform.openai.com/ |

---

## 四、各模块安装

### 模块一：Intel Briefing（情报日报）

```bash
cd /path/to/Intel_Briefing
pip install -r requirements.txt

# 验证
python -c "import httpx; print('OK')"
```

**必需**：`.env` 中配置 `GITHUB_TOKEN`。

---

### 模块二：OpenCLI（平台内容获取）

```bash
# 安装 CLI
npm install -g @jackwener/opencli

# 验证
opencli --version

# 安装 Chrome 扩展（必须）
# 1. Chrome → 地址栏输入 chrome://extensions
# 2. 开启右上角"开发者模式"
# 3. 点击"加载已解压的扩展程序"
# 4. 选择目录：/path/to/opencli/extension/
# 5. 确认扩展出现在列表中并已启用

# 验证浏览器连接
opencli doctor
```

**注意**：使用 B站/知乎/Twitter 等命令前，需在 Chrome 中**登录对应平台**。

---

### 模块三：UltimateSearchSkill（双引擎深度搜索）

```bash
# 无需额外安装 Python 包，只需确认 .env 中配置：
# GROK_API_KEY 和 GROK_API_URL
# TAVILY_API_KEY 和 TAVILY_API_URL

# 验证脚本可执行
ls /path/to/UltimateSearchSkill/scripts/
```

**推荐配置（直连模式，无需本地代理）：**
```
GROK_API_URL=https://api.x.ai/v1
GROK_MODEL=grok-3
TAVILY_API_URL=https://api.tavily.com
```

---

### 模块四：Union Search（多平台联合搜索）

```bash
pip install requests python-dotenv duckduckgo-search

# GitHub 搜索（可选）
pip install PyGithub

# YouTube 搜索（可选）
pip install yt-dlp

# 验证基础功能（无需 API Key）
cd /path/to/union-search-skill
python scripts/duckduckgo/duckduckgo_search.py "hello" --limit 3
```

---

### 模块五：Agent-Reach（网页/视频/RSS访问）

```bash
# 安装主包
pip install agent-reach

# 自动安装所有下游工具
agent-reach install --env=auto

# 或手动安装
pip install yt-dlp feedparser
npm install -g @steipete/bird
brew install gh && gh auth login

# 验证
agent-reach doctor
```

---

### 模块六：last30days（30天话题研究）

```bash
pip install yt-dlp requests python-dotenv

# 验证（会显示帮助或运行）
python3 /path/to/last30days-skill/scripts/last30days.py --help 2>/dev/null || true
```

---

### 模块七：News Aggregator（新闻聚合）

```bash
pip install -r /path/to/news-aggregator-skill/requirements.txt

# HuggingFace Papers / Ben's Bites 需要 Playwright（可选）
pip install playwright && playwright install chromium

# 验证（抓取 HN 前3条，无需任何 Key）
cd /path/to/news-aggregator-skill
python3 scripts/fetch_news.py --source hackernews --limit 3 --no-save
```

---

## 五、Claude Code 接入

### 安装 Claude Code

```bash
npm install -g @anthropic-ai/claude-code
```

详细安装文档：https://docs.anthropic.com/claude/docs/claude-code

### 放置技能树

```bash
mkdir -p ~/.claude/skills
cp -r web-search-innospark-tree ~/.claude/skills/
```

### 启动 Claude Code

```bash
claude
```

首次启动会要求登录 Anthropic 账号。登录后，在对话中输入 `/web-search-innospark-tree` 即可触发技能树。

---

## 六、验证安装

按优先级依次验证，每步成功后再继续：

```bash
# 1. 最基础：HN 新闻（无需任何 Key）
cd /path/to/news-aggregator-skill
python3 scripts/fetch_news.py --source hackernews --limit 3 --no-save

# 2. DuckDuckGo 搜索（无需任何 Key）
python3 /path/to/union-search-skill/scripts/duckduckgo/duckduckgo_search.py "AI" --limit 3

# 3. GitHub Trending（需要 GITHUB_TOKEN）
cd /path/to/Intel_Briefing && python run_mission.py

# 4. OpenCLI（需要 Chrome 扩展）
opencli hackernews top --limit 5

# 5. 深度搜索（需要 GROK_API_KEY 或 TAVILY_API_KEY）
bash /path/to/UltimateSearchSkill/scripts/grok-search.sh --query "test"
```

---

## 七、常见问题

### Q: `ModuleNotFoundError: No module named 'xxx'`
```bash
pip install xxx
# 或重装所有依赖
pip install -r /path/to/<对应模块>/requirements.txt
```

### Q: `command not found: opencli`
```bash
npm install -g @jackwener/opencli
```

### Q: `command not found: node` / `npm`
```bash
# macOS
brew install node
# Windows: 从 https://nodejs.org/ 下载安装包
```

### Q: OpenCLI `Extension not connected`
1. 确认 Chrome 正在运行
2. 确认 Browser Bridge 扩展已安装并**启用**
3. 运行 `opencli doctor` 查看详细诊断

### Q: GitHub API 报 `GITHUB_TOKEN not set`
在 `.env` 中添加：`GITHUB_TOKEN=your_github_token_here`，然后重新 `source .env`。

### Q: Twitter/X 数据为空
Intel Briefing 的 Twitter 板块需要 `XAI_API_KEY`，缺少时该板块自动跳过，其他板块不受影响。

### Q: 在中国大陆访问部分平台受限
在 `.env` 中取消注释并填写代理：
```bash
HTTP_PROXY=http://127.0.0.1:7890
HTTPS_PROXY=http://127.0.0.1:7890
```
常用代理客户端端口：Clash HTTP=7890 | V2RayN HTTP=10809

### Q: Reddit 返回 403
```bash
agent-reach configure proxy "http://用户名:密码@IP:端口"
```

---

## 最小配置（只申请必需 Key 即可启动）

如果不想一次申请太多，最小配置只需：

| Key | 申请 | 覆盖功能 |
|-----|------|---------|
| `GITHUB_TOKEN` | GitHub 免费 | Intel Briefing、GitHub 搜索 |
| `XAI_API_KEY` | xAI $0/月（有免费额度） | Grok 搜索、AI 分析 |
| `TAVILY_API_KEY` | Tavily 免费 1000次/月 | 结构化搜索 |

其余所有模块（News Aggregator、DuckDuckGo、网页读取、视频字幕等）**无需任何 Key 即可使用**。

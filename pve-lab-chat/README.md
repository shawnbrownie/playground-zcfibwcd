# pve-lab-chat

给 pve-lab 上 **ai01** 用的轻量聊天网页。在外面用 Tailscale 连回家，打开浏览器（手机/电脑都行）就能问问题。

```
手机 / 笔记本 ──Tailscale (HTTPS, tailscale serve)──▶ ai01: pve-lab-chat 127.0.0.1:8080
                                                        └──▶ llama-server 127.0.0.1:8081 (GPU)
```

推荐把它**装在 ai01 本机**：模型 API 继续只监听 `127.0.0.1`，不暴露给局域网；对外只通过 `tailscale serve` 发布这个网页（tailnet 私有 HTTPS，不是 Funnel）。

- 纯 Python 标准库，**零依赖**，一个 `server.py` + 一个 `index.html`
- 流式输出、随时「停止」、Markdown / 代码块渲染与一键复制
- 推理模型（Qwen3 等）的思考过程单独折叠显示（支持 `reasoning_content` 和 `<think>` 标签）
- 多对话、模型下拉、System prompt、Temperature；记录只存在你自己浏览器里
- 后端：**OpenAI 兼容**（llama.cpp `llama-server`、vLLM、LM Studio，默认）或 **Ollama**
- 安全：默认只监听 127.0.0.1、可选 `ACCESS_TOKEN` 口令、`MAX_CONCURRENT` 并发上限（超出返回 429）

## 部署（在 ai01 上）

### 0. 只读预检

```bash
systemctl is-active local-fast.service           # 模型服务（按你的实际服务名）
curl --fail --max-time 10 http://127.0.0.1:8081/v1/models
nvidia-smi                                       # 确认在用 GPU，而不是 CPU fallback
ss -ltnp | grep -E ':8080\b' || echo "8080 空闲"
command -v tailscale && tailscale status | head -3
```

### 1. 安装

```bash
git clone -b claude/pve-lab-chatbot-interface-5sbdit https://github.com/shawnbrownie/playground-zcfibwcd.git
cd playground-zcfibwcd/pve-lab-chat
python3 -m unittest test_server.py               # 可选：本地自测
sudo ./install.sh
sudoedit /etc/pve-lab-chat.env                   # 设置 ACCESS_TOKEN（openssl rand -hex 16），按需改 DEFAULT_MODEL
sudo systemctl restart pve-lab-chat
curl -s http://127.0.0.1:8080/api/models -H "Authorization: Bearer <你的口令>"
```

Docker 方式：`cp pve-lab-chat.env.example .env && docker compose up -d`（使用 host 网络以访问 127.0.0.1:8081）。

### 2. 通过 Tailscale 私有发布

```bash
# ai01 还没加入 tailnet 的话：
curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up
sudo tailscale serve --bg 8080
tailscale serve status
```

手机/电脑连上 Tailscale 后访问 `https://ai01.<你的tailnet>.ts.net/`（需在 Tailscale 后台开启 HTTPS Certificates）。
iPhone 上可「添加到主屏幕」当 App 用。**不要用 `tailscale funnel`。**

### 回滚

```bash
sudo tailscale serve reset            # 若 ai01 上 serve 只用于本服务；否则用 tailscale serve --https=443 off
sudo systemctl disable --now pve-lab-chat
sudo rm -rf /opt/pve-lab-chat /etc/systemd/system/pve-lab-chat.service /etc/pve-lab-chat.env
sudo systemctl daemon-reload
```

模型服务（`local-fast.service`）和它的配置在整个过程中都不会被改动。

## 配置项（`/etc/pve-lab-chat.env`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `BACKEND_URL` | `http://127.0.0.1:8081` | 模型服务地址 |
| `BACKEND_TYPE` | `openai` | `openai`（llama-server/vLLM/LM Studio）或 `ollama` |
| `API_KEY` | 空 | 后端用 `--api-key` 启动时填 |
| `DEFAULT_MODEL` | 空 | 默认选中的模型 id，空 = 第一个 |
| `SYSTEM_PROMPT` | … | 新对话默认 system prompt（网页设置里可覆盖） |
| `CHAT_TITLE` | `pve-lab · ai01` | 网页标题 |
| `HOST` / `PORT` | `127.0.0.1` / `8080` | 监听地址；`0.0.0.0` 会暴露给整个局域网 |
| `ACCESS_TOKEN` | 空 | 口令（Tailscale 之外的第二层），网页「设置」里输入 |
| `MAX_CONCURRENT` | `2` | 同时生成的请求上限，超出返回 429 |
| `BACKEND_TIMEOUT` | `600` | 后端请求超时秒数 |

## 排错

- 左上角圆点**红色**：连不上模型服务。悬停看错误；`curl http://127.0.0.1:8081/v1/models`。
- 回答特别慢：检查 `nvidia-smi` 里有没有 llama-server 进程占显存——服务可能在 GPU 就绪前启动，退回了 CPU；在 ai01 内重启模型服务即可（先确认再操作）。
- 手机打不开但服务正常：iPhone 上把 Tailscale 关掉约 10 秒再打开。

## 测试

```bash
python3 -m unittest test_server.py -v
```

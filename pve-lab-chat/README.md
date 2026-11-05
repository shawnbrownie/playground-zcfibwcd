# pve-lab-chat

给 pve-lab 上 **ai01** 用的轻量聊天网页。在外面用 Tailscale 连回家，打开浏览器（手机/电脑都行）就能问问题。

```
手机 / 笔记本 ──Tailscale──▶ pve-lab-chat (:8080) ──LAN──▶ ai01 (Ollama :11434 或 OpenAI 兼容 API)
```

- 纯 Python 标准库，**零依赖**，一个 `server.py` + 一个 `index.html`
- 流式输出、可随时「停止」、Markdown / 代码块渲染与一键复制
- 多对话、模型下拉选择、System prompt、Temperature
- 对话记录只存在你自己浏览器的 localStorage，服务器不落盘
- 支持 **Ollama**（默认）和 **OpenAI 兼容接口**（vLLM / llama.cpp server / LM Studio / LocalAI）
- 手机适配，深色模式自动跟随系统

## 1. 确认 ai01 的 API 能从 LAN 访问

如果 ai01 跑的是 Ollama，默认只监听 `127.0.0.1`，需要改成监听所有网卡（在 ai01 上）：

```bash
sudo systemctl edit ollama
# 加入：
# [Service]
# Environment="OLLAMA_HOST=0.0.0.0:11434"
sudo systemctl restart ollama
```

在准备部署 chat 的机器上测试：

```bash
curl http://ai01:11434/api/tags      # Ollama
curl http://ai01:8000/v1/models      # vLLM / OpenAI 兼容（端口按你的实际情况）
```

> `ai01` 这个主机名解析不了就直接用它的 LAN IP（例如 `http://192.168.1.50:11434`）。

## 2. 部署

可以装在 **ai01 本身**、pve-lab 宿主机、或一个小 LXC 里——只要这台机器同时能访问 ai01 并且装了 Tailscale。最省事的是直接装在 ai01 上。

```bash
git clone <this repo> && cd <repo>/pve-lab-chat
sudo ./install.sh
sudo nano /etc/pve-lab-chat.env      # 改 BACKEND_URL / BACKEND_TYPE / DEFAULT_MODEL
sudo systemctl restart pve-lab-chat
journalctl -u pve-lab-chat -f        # 看日志
```

或者用 Docker：

```bash
cp pve-lab-chat.env.example .env && nano .env
docker compose up -d
```

或者临时手动跑：

```bash
BACKEND_URL=http://ai01:11434 python3 server.py
```

## 3. 通过 Tailscale 从外网访问

部署的机器要先加入你的 tailnet：

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

> 在 Proxmox **LXC** 里跑 Tailscale 需要给容器开 `/dev/net/tun`，在宿主机 `/etc/pve/lxc/<CTID>.conf` 加：
> ```
> lxc.cgroup2.devices.allow: c 10:200 rwm
> lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
> ```

然后二选一：

**A. 直接用 Tailscale IP / MagicDNS（最简单）**

```
http://<机器名>:8080        # 开了 MagicDNS
http://100.x.y.z:8080       # tailscale ip -4 查看
```

**B. 用 `tailscale serve` 套 HTTPS（推荐，手机上体验更好）**

```bash
# /etc/pve-lab-chat.env 里设置 HOST=127.0.0.1，只让 tailscale 访问
sudo systemctl restart pve-lab-chat
sudo tailscale serve --bg 8080
```

之后访问 `https://<机器名>.<你的tailnet>.ts.net` 即可（需要在 Tailscale 后台开启 HTTPS Certificates）。
在手机浏览器里「添加到主屏幕」就像一个 App。

> ⚠ 不要用 `tailscale funnel`——那会把它暴露到公网。

## 配置项（`/etc/pve-lab-chat.env`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `BACKEND_URL` | `http://ai01:11434` | ai01 上模型服务的地址 |
| `BACKEND_TYPE` | `ollama` | `ollama` 或 `openai` |
| `API_KEY` | 空 | OpenAI 兼容后端需要 key 时填 |
| `DEFAULT_MODEL` | 空 | 默认选中的模型，空 = 列表第一个 |
| `SYSTEM_PROMPT` | … | 新对话默认 system prompt（网页设置里可覆盖） |
| `CHAT_TITLE` | `pve-lab · ai01` | 网页标题 |
| `HOST` / `PORT` | `0.0.0.0` / `8080` | 监听地址；用 `tailscale serve` 时设成 `127.0.0.1` |
| `ACCESS_TOKEN` | 空 | 可选的额外口令（Tailscale 之外再加一层），网页「设置」里输入 |
| `BACKEND_TIMEOUT` | `600` | 后端请求超时秒数 |

## 排错

- 左上角圆点 **红色**：chat 服务连不上 ai01。鼠标悬停看错误，或 `curl $BACKEND_URL/api/tags`。
- 下拉框显示「ai01 上没有模型」：在 ai01 上 `ollama pull qwen2.5:7b` 之类拉一个模型。
- 外网打不开：确认手机/电脑的 Tailscale 已连接，`tailscale status` 能看到这台机器；检查防火墙是否放行 8080。

## 测试

```bash
python3 -m unittest test_server.py -v
```

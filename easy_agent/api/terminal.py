"""Web Terminal - 独立终端页面 + WebSocket 接口

通过 FastAPI 提供独立的终端 HTML 页面，使用 xterm.js (CDN) + WebSocket 实现交互式终端。
访问地址: http://host:port/terminal

这是宿主机上的真实 shell（不经沙箱、继承服务进程环境），因此（SEC-01）：
- 默认关闭，``EASY_WEB_TERMINAL_ENABLED=1`` 才挂出页面与 WebSocket；
- 连接后首帧必须是 ``{"type": "auth", "token": "<JWT>"}``，且只允许 admin；
  鉴权通过前不创建 pty / 子进程。
"""

import asyncio
import json
import logging
import os
import struct
import termios
import pty
import select
import fcntl
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.websockets import WebSocketState

from easy_agent.bootstrap.env import env_bool
from easy_agent.db.repositories.user_repository import UserRepository
from easy_agent.middleware.auth import get_user_repository, verify_token_sso

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Terminal"])

_ENABLED_ENV = "EASY_WEB_TERMINAL_ENABLED"
_ADMIN_USERNAME = "admin"
_AUTH_TIMEOUT_SECONDS = 10
# 应用自定义关闭码（4000-4999）：未登录 / 非管理员
_CLOSE_UNAUTHENTICATED = 4401
_CLOSE_FORBIDDEN = 4403


def web_terminal_enabled() -> bool:
    """Web 终端开关，默认关闭（SEC-01）。"""
    return env_bool(_ENABLED_ENV, False)


TERMINAL_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Web Terminal - Easy Agent</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: #1e1e2e;
            color: #cdd6f4;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            height: 100vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }
        .terminal-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 8px 16px;
            background: #181825;
            border-bottom: 1px solid #313244;
            flex-shrink: 0;
        }
        .terminal-title {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 14px;
            font-weight: 500;
        }
        .terminal-title svg { width: 18px; height: 18px; }
        .terminal-status {
            font-size: 11px;
            padding: 2px 8px;
            border-radius: 10px;
        }
        .terminal-status.connected {
            background: rgba(166, 227, 161, 0.15);
            color: #a6e3a1;
        }
        .terminal-status.disconnected {
            background: rgba(243, 139, 168, 0.15);
            color: #f38ba8;
        }
        .terminal-actions { display: flex; gap: 4px; }
        .terminal-btn {
            display: flex;
            align-items: center;
            justify-content: center;
            width: 32px;
            height: 32px;
            border: none;
            background: transparent;
            color: #9399b2;
            cursor: pointer;
            border-radius: 6px;
            transition: all 0.15s;
        }
        .terminal-btn svg { width: 16px; height: 16px; }
        .terminal-btn:hover { background: #313244; color: #cdd6f4; }
        #terminal-container {
            flex: 1;
            padding: 8px 12px;
            overflow: hidden;
        }
        .terminal-info {
            padding: 4px 16px;
            background: #181825;
            border-top: 1px solid #313244;
            font-size: 12px;
            color: #6c7086;
            flex-shrink: 0;
        }
    </style>
</head>
<body>
    <div class="terminal-header">
        <div class="terminal-title">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="4 17 10 11 4 5"></polyline>
                <line x1="12" y1="19" x2="20" y2="19"></line>
            </svg>
            <span>Web Terminal</span>
            <span id="status" class="terminal-status disconnected">未连接</span>
        </div>
        <div class="terminal-actions">
            <button class="terminal-btn" id="restart-btn" title="重启终端">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="23 4 23 10 17 10"></polyline>
                    <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
                </svg>
            </button>
            <button class="terminal-btn" id="clear-btn" title="清屏">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6h14z"></path>
                </svg>
            </button>
        </div>
    </div>
    <div id="terminal-container"></div>
    <div class="terminal-info">
        <span>默认路径: ~ | 支持交互式命令 (ls, tail -f, top, vim, cd 等)</span>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/@xterm/addon-web-links@0.11.0/lib/addon-web-links.min.js"></script>
    <script>
        const term = new Terminal({
            cursorBlink: true,
            fontSize: 15,
            fontFamily: "'Cascadia Code', 'Fira Code', 'JetBrains Mono', 'Consolas', 'Courier New', monospace",
            theme: {
                background: '#1e1e2e',
                foreground: '#cdd6f4',
                cursor: '#f5e0dc',
                selectionBackground: '#585b70',
                black: '#45475a', red: '#f38ba8', green: '#a6e3a1',
                yellow: '#f9e2af', blue: '#89b4fa', magenta: '#f5c2e7',
                cyan: '#94e2d5', white: '#bac2de',
                brightBlack: '#585b70', brightRed: '#f38ba8', brightGreen: '#a6e3a1',
                brightYellow: '#f9e2af', brightBlue: '#89b4fa', brightMagenta: '#f5c2e7',
                brightCyan: '#94e2d5', brightWhite: '#a6adc8',
            },
            allowProposedApi: true,
        });
        const fitAddon = new FitAddon.FitAddon();
        term.loadAddon(fitAddon);
        term.loadAddon(new WebLinksAddon.WebLinksAddon());
        term.open(document.getElementById('terminal-container'));
        fitAddon.fit();

        let ws = null;
        const statusEl = document.getElementById('status');

        // 鉴权 token：与主界面同源时复用其登录态；前后端不同源（开发环境）可用
        // /terminal#token=<JWT>（fragment 不会发给服务器），读取后从地址栏移除。
        function readToken() {
            const m = location.hash.match(/token=([^&]+)/);
            if (m) {
                history.replaceState(null, '', location.pathname);
                return decodeURIComponent(m[1]);
            }
            try { return localStorage.getItem('mini_agent_token') || ''; } catch (e) { return ''; }
        }
        const authToken = readToken();

        function getWsBase() {
            const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
            return proto + '//' + location.host;
        }

        function connectWS() {
            if (ws) { try { ws.close(); } catch(e) {} }
            ws = new WebSocket(getWsBase() + '/agent/terminal/ws');
            ws.binaryType = 'arraybuffer';

            ws.onopen = () => {
                if (!authToken) {
                    term.write('\\r\\n\\x1b[31m[未找到登录凭证：请先在主界面用 admin 账号登录]\\x1b[0m\\r\\n');
                    ws.close();
                    return;
                }
                // 首帧必须是鉴权消息，服务端校验通过（仅 admin）后才创建 shell
                ws.send(JSON.stringify({ type: 'auth', token: authToken }));
                statusEl.textContent = '已连接';
                statusEl.className = 'terminal-status connected';
                term.focus();
            };
            ws.onmessage = (event) => {
                if (event.data instanceof ArrayBuffer) {
                    term.write(new Uint8Array(event.data));
                } else {
                    term.write(event.data);
                }
            };
            ws.onerror = () => {
                statusEl.textContent = '错误';
                statusEl.className = 'terminal-status disconnected';
            };
            ws.onclose = () => {
                statusEl.textContent = '已断开';
                statusEl.className = 'terminal-status disconnected';
                term.write('\\r\\n\\x1b[33m[连接已断开]\\x1b[0m\\r\\n');
            };
        }

        term.onData((data) => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(data);
            }
        });

        // 窗口大小变化时自适应
        window.addEventListener('resize', () => { fitAddon.fit(); });
        const resizeObserver = new ResizeObserver(() => { fitAddon.fit(); });
        resizeObserver.observe(document.getElementById('terminal-container'));

        // 按钮事件
        document.getElementById('restart-btn').addEventListener('click', () => {
            if (ws) { try { ws.close(); } catch(e) {} }
            term.clear();
            connectWS();
        });
        document.getElementById('clear-btn').addEventListener('click', () => {
            term.clear();
        });

        // 直接连接
        connectWS();
    </script>
</body>
</html>"""


@router.get("/terminal", response_class=HTMLResponse, summary="Web 终端页面")
async def terminal_page():
    """独立的 Web 终端页面，使用 xterm.js + WebSocket 实现交互式终端。"""
    if not web_terminal_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    return HTMLResponse(content=TERMINAL_HTML)


@router.websocket("/agent/terminal/ws")
async def terminal_ws(
    websocket: WebSocket,
    users: Annotated[UserRepository, Depends(get_user_repository)],
):
    """WebSocket 终端接口：首帧 admin 鉴权通过后才创建 shell。

    浏览器的 WebSocket 不能带 Authorization 头，token 放 URL 又会进访问日志，
    所以先 accept，再收一帧 ``{"type": "auth", "token": "<JWT>"}``。
    """
    if not web_terminal_enabled():
        await websocket.close(code=1008)  # accept 前关闭 = 拒绝握手
        return
    await websocket.accept()
    web_user = await _authenticate_admin(websocket, users)
    if web_user is None:
        return
    await _run_shell_session(websocket, web_user)


async def _authenticate_admin(websocket: WebSocket, users: UserRepository) -> str | None:
    """校验首帧 token（含单点登录 / 停用 / 空闲过期），只放行 admin；失败时关闭连接。"""
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=_AUTH_TIMEOUT_SECONDS)
        message = json.loads(raw)
    except (asyncio.TimeoutError, WebSocketDisconnect, KeyError, RuntimeError, ValueError):
        message = None
    token = message.get("token") if isinstance(message, dict) and message.get("type") == "auth" else None
    username = verify_token_sso(token, users) if isinstance(token, str) and token else None
    if username is None:
        await _reject(websocket, _CLOSE_UNAUTHENTICATED, "未登录或登录已过期")
        return None
    if username != _ADMIN_USERNAME:
        await _reject(websocket, _CLOSE_FORBIDDEN, "仅管理员可使用 Web 终端")
        return None
    return username


async def _reject(websocket: WebSocket, code: int, reason: str) -> None:
    logger.warning(f"拒绝 Web 终端连接 | {reason} | client={websocket.client}")
    try:
        await websocket.send_text(f"\r\n\x1b[31m[{reason}]\x1b[0m\r\n")
        await websocket.close(code=code, reason=reason)
    except Exception:  # noqa: BLE001 - 客户端可能已断开
        pass


async def _run_shell_session(websocket: WebSocket, web_user: str) -> None:
    """使用 pty 创建伪终端，支持交互式命令（ls, tail -f, top, vim 等）。

    默认工作目录为服务进程用户的 home 目录 (~)。
    """
    # 默认使用用户 home 目录
    cwd = os.path.expanduser("~")

    # 获取当前系统用户名
    import getpass
    username = getpass.getuser()

    master_fd, slave_fd = pty.openpty()

    # 设置窗口大小（默认 80x24）
    winsize = struct.pack("HHHH", 24, 80, 0, 0)
    fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

    # 启动 shell 子进程
    env = os.environ.copy()
    env["TERM"] = "xterm-256color"
    env["HOME"] = os.path.expanduser("~")
    env["USER"] = username
    env["LANG"] = "en_US.UTF-8"
    env["LC_ALL"] = "en_US.UTF-8"

    pid = os.fork()
    if pid == 0:
        # 子进程
        os.close(master_fd)
        os.setsid()

        # 设置控制终端
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)

        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        if slave_fd > 2:
            os.close(slave_fd)

        os.chdir(cwd)
        try:
            os.execvpe("bash", ["bash", "--login"], env)
        except FileNotFoundError:
            os.execvpe("sh", ["sh"], env)
        os._exit(1)

    # 父进程
    os.close(slave_fd)

    logger.info(f"终端会话已创建 | web_user={web_user} | os_user={username} | pid={pid} | cwd={cwd}")

    async def read_from_pty():
        """从 pty 读取输出并发送到 WebSocket"""
        loop = asyncio.get_event_loop()
        while True:
            try:
                rlist, _, _ = await loop.run_in_executor(
                    None, lambda: select.select([master_fd], [], [], 0.1)
                )
                if rlist:
                    data = os.read(master_fd, 65536)
                    if not data:
                        break
                    await websocket.send_bytes(data)
            except OSError:
                break
            except Exception as e:
                logger.error(f"读取终端输出失败: {e}")
                break

    async def write_to_pty():
        """从 WebSocket 读取输入并写入 pty"""
        try:
            while True:
                msg = await websocket.receive()
                if msg["type"] == "websocket.disconnect":
                    break

                if "bytes" in msg and msg["bytes"] is not None:
                    os.write(master_fd, msg["bytes"])
                elif "text" in msg and msg["text"] is not None:
                    text = msg["text"]
                    try:
                        data = text.encode("utf-8")
                        os.write(master_fd, data)
                    except Exception:
                        pass
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"读取 WebSocket 输入失败: {e}")

    try:
        read_task = asyncio.create_task(read_from_pty())
        write_task = asyncio.create_task(write_to_pty())

        done, pending = await asyncio.wait(
            [read_task, write_task], return_when=asyncio.FIRST_COMPLETED
        )

        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    except Exception as e:
        logger.error(f"终端会话异常: {e}")
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass
        try:
            os.kill(pid, 9)
            os.waitpid(pid, 0)
        except (OSError, ChildProcessError):
            pass

        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.close()
            except Exception:
                pass

        logger.info(f"终端会话已关闭 | web_user={web_user} | pid={pid}")

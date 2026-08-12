"""Nginx 启停管理公共方法：检测启动方式并执行 reload/restart/start/stop。"""
import re
import shlex

from utils.ssh import SSHClient

# 常见 systemd unit 名（按优先级探测）
_DEFAULT_UNITS = ("nginx", "nginx.service")

# mngxops 托管的 systemd unit 路径
SYSTEMD_UNIT_DIR = "/etc/systemd/system"
DEFAULT_UNIT_NAME = "nginx"
UNIT_FILE_PATH = "/etc/systemd/system/nginx.service"


def _auth_kwargs(password=None, private_key=None):
    """组装 SSH 认证参数"""
    kwargs = {}
    if password is not None:
        kwargs["password"] = password
    if private_key is not None:
        kwargs["private_key"] = private_key
    return kwargs


def _run(ssh, command):
    """执行远程命令，返回 (success, output)"""
    return ssh.execute_command(command)


def _systemd_cmd(cmd, use_sudo=False):
    """按需为命令加 sudo -n 前缀"""
    cmd = (cmd or "").strip()
    if not cmd:
        return cmd
    if use_sudo:
        return f"sudo -n {cmd}"
    return cmd


def can_manage_systemd_unit(ssh, log_fn=None):
    """探测当前 SSH 用户是否可管理系统 unit。

    Returns:
        tuple: (ok, use_sudo, reason)
    """
    def _log(msg):
        if log_fn:
            log_fn(msg)

    ok, out = _run(ssh, "command -v systemctl >/dev/null 2>&1 && echo OK")
    if not (ok and "OK" in (out or "")):
        _log("未找到 systemctl")
        return False, False, "未找到 systemctl"

    ok, out = _run(
        ssh,
        f"test -w {shlex.quote(SYSTEMD_UNIT_DIR)} >/dev/null 2>&1 && echo OK",
    )
    if ok and "OK" in (out or ""):
        _log("可写 unit 目录，无需 sudo")
        return True, False, "可写 unit 目录"

    ok, out = _run(ssh, "sudo -n true >/dev/null 2>&1 && echo OK")
    if ok and "OK" in (out or ""):
        _log("免密 sudo 可用")
        return True, True, "免密 sudo 可用"

    _log("无 unit 写权限且无免密 sudo")
    return False, False, "无 unit 写权限且无免密 sudo"


def build_nginx_unit_content(nginx_bin, user="", group=""):
    """生成 nginx.service 文件内容"""
    bin_path = (nginx_bin or "nginx").strip() or "nginx"
    lines = [
        "[Unit]",
        "Description=nginx (managed by mngxops)",
        "After=network.target",
        "",
        "[Service]",
        "Type=forking",
        f"ExecStart={bin_path}",
        f"ExecReload={bin_path} -s reload",
        f"ExecStop={bin_path} -s quit",
        "KillMode=mixed",
        "PrivateTmp=true",
    ]
    user = (user or "").strip()
    group = (group or "").strip()
    if user:
        lines.append(f"User={user}")
    if group:
        lines.append(f"Group={group}")
    lines.extend([
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ])
    return "\n".join(lines)


def write_nginx_systemd_unit(
    ssh,
    *,
    nginx_bin,
    user="",
    group="",
    unit_name=DEFAULT_UNIT_NAME,
    use_sudo=False,
    log_fn=None,
):
    """写入并 enable nginx systemd unit。

    Returns:
        tuple: (ok, msg)
    """
    def _log(msg):
        if log_fn:
            log_fn(msg)

    unit = (unit_name or DEFAULT_UNIT_NAME).replace(".service", "")
    content = build_nginx_unit_content(nginx_bin, user=user, group=group)
    path = UNIT_FILE_PATH
    _log(f"写入 systemd unit: {path}（若已存在将覆盖）")

    # 固定路径 + 单引号 heredoc，避免内容注入
    write_cmd = (
        f"cat > {shlex.quote(path)} <<'MNGXOPS_UNIT_EOF'\n"
        f"{content}"
        f"MNGXOPS_UNIT_EOF"
    )
    if use_sudo:
        # tee 写入受保护目录
        write_cmd = (
            f"cat <<'MNGXOPS_UNIT_EOF' | sudo -n tee {shlex.quote(path)} >/dev/null\n"
            f"{content}"
            f"MNGXOPS_UNIT_EOF"
        )

    ok, out = _run(ssh, write_cmd)
    if not ok:
        return False, f"写入 unit 失败: {out}"

    reload_cmd = _systemd_cmd("systemctl daemon-reload 2>&1", use_sudo=use_sudo)
    _log(f"执行: {reload_cmd}")
    ok, out = _run(ssh, reload_cmd)
    if not ok:
        return False, f"daemon-reload 失败: {out}"

    enable_cmd = _systemd_cmd(f"systemctl enable {unit} 2>&1", use_sudo=use_sudo)
    _log(f"执行: {enable_cmd}")
    ok, out = _run(ssh, enable_cmd)
    if not ok:
        return False, f"systemctl enable 失败: {out}"

    return True, (out or "").strip() or f"已注册并 enable {unit}"


def remove_nginx_systemd_unit(
    ssh,
    *,
    unit_name=DEFAULT_UNIT_NAME,
    use_sudo=False,
    log_fn=None,
):
    """disable 并删除可管的 nginx unit 文件。

    Returns:
        tuple: (ok, msg)
    """
    def _log(msg):
        if log_fn:
            log_fn(msg)

    unit = (unit_name or DEFAULT_UNIT_NAME).replace(".service", "")
    notes = []

    disable_cmd = _systemd_cmd(
        f"systemctl disable {unit} 2>&1 || true",
        use_sudo=use_sudo,
    )
    _log(f"执行: {disable_cmd}")
    ok, out = _run(ssh, disable_cmd)
    if out:
        _log(f"disable 输出: {(out or '').strip()}")
    if not ok:
        notes.append(f"disable 非零退出: {(out or '').strip()}")

    # FragmentPath
    show_cmd = _systemd_cmd(
        f"systemctl show -p FragmentPath --value {unit} 2>/dev/null || true",
        use_sudo=use_sudo,
    )
    ok, frag_out = _run(ssh, show_cmd)
    fragment = (frag_out or "").strip().splitlines()
    fragment_path = fragment[-1].strip() if fragment else ""

    to_delete = set()
    # 始终尝试删除平台托管路径
    to_delete.add(UNIT_FILE_PATH)
    if fragment_path.startswith(f"{SYSTEMD_UNIT_DIR}/"):
        to_delete.add(fragment_path)
    elif fragment_path.startswith("/lib/") or fragment_path.startswith("/usr/lib/"):
        notes.append(f"发行版 unit 未物理删除: {fragment_path}")
        _log(notes[-1])
        to_delete.discard(fragment_path)

    for path in sorted(to_delete):
        check_cmd = f"test -e {shlex.quote(path)} && echo EXISTS || echo MISSING"
        ok, cout = _run(ssh, check_cmd)
        if "EXISTS" not in (cout or ""):
            _log(f"unit 文件不存在，跳过: {path}")
            continue
        rm_cmd = _systemd_cmd(f"rm -f {shlex.quote(path)} 2>&1", use_sudo=use_sudo)
        _log(f"执行: {rm_cmd}")
        ok, out = _run(ssh, rm_cmd)
        if not ok:
            return False, f"删除 unit 失败 ({path}): {out}"

    reload_cmd = _systemd_cmd("systemctl daemon-reload 2>&1", use_sudo=use_sudo)
    _log(f"执行: {reload_cmd}")
    ok, out = _run(ssh, reload_cmd)
    if not ok:
        return False, f"daemon-reload 失败: {out}"

    msg = f"已清理 systemd unit {unit}"
    if notes:
        msg = f"{msg}（{'; '.join(notes)}）"
    return True, msg


def _wrap_paramiko_client(host, port, username, client):
    """将已连接的 paramiko 客户端包装为 SSHClient（不负责关闭）"""
    ssh = SSHClient(host or "", port or 22, username or "")
    ssh.client = client
    return ssh


def _open_ssh(host, port, username, password=None, private_key=None, client=None):
    """获取 SSHClient；传入 client 时复用，返回 (ssh, owns)"""
    if client is not None:
        return _wrap_paramiko_client(host, port, username, client), False
    auth = _auth_kwargs(password, private_key)
    ssh = SSHClient(host, port, username, **auth)
    ok, msg = ssh.connect()
    if not ok:
        raise ConnectionError(msg or "SSH 连接失败")
    return ssh, True


def _unit_candidates(unit_name=None):
    """组装待探测的 systemd unit 名列表"""
    candidates = []
    if unit_name:
        candidates.append(unit_name.replace(".service", ""))
    for u in _DEFAULT_UNITS:
        name = u.replace(".service", "")
        if name not in candidates:
            candidates.append(name)
    return candidates


def _detect_mode_with_ssh(ssh, candidates, log_fn=None):
    """在已连接 SSH 上检测 Nginx 启动方式"""
    def _log(msg):
        if log_fn:
            log_fn(msg)

    try:
        ok, _ = _run(ssh, "command -v systemctl >/dev/null 2>&1 && echo OK")
        if ok and "OK" in (_ or ""):
            for name in candidates:
                ok, out = _run(
                    ssh,
                    f"systemctl is-active {name} 2>/dev/null || true",
                )
                state = (out or "").strip().splitlines()[-1] if out else ""
                if state in ("active", "activating", "reloading"):
                    _log(f"检测到 Nginx 由 systemctl 托管 (unit={name}, state={state})")
                    return "systemctl", {"unit": name, "state": state}
                ok, en = _run(
                    ssh,
                    f"systemctl is-enabled {name} 2>/dev/null || true",
                )
                en_state = (en or "").strip().splitlines()[-1] if en else ""
                if en_state in ("enabled", "enabled-runtime", "static"):
                    _log(f"检测到 Nginx systemd unit 已启用 (unit={name})，按 systemctl 管理")
                    return "systemctl", {"unit": name, "state": state or en_state}
    except Exception as e:
        _log(f"探测 systemctl 异常，回退为二进制方式: {e}")

    _log("未检测到 systemctl 托管，按直接二进制管理")
    return "binary", {"unit": ""}


def detect_nginx_manage_mode(
    host,
    port,
    username,
    password=None,
    private_key=None,
    unit_name=None,
    log_fn=None,
    client=None,
):
    """检测远程 Nginx 启动方式

    Returns:
        tuple: (mode, detail)
            mode: "systemctl" | "binary"
            detail: dict，含 unit（systemctl 时）等说明
    """
    def _log(msg):
        if log_fn:
            log_fn(msg)

    candidates = _unit_candidates(unit_name)
    owns = False
    ssh = None
    try:
        ssh, owns = _open_ssh(
            host, port, username, password=password, private_key=private_key, client=client,
        )
        return _detect_mode_with_ssh(ssh, candidates, log_fn=log_fn)
    except Exception as e:
        _log(f"探测 systemctl 异常，回退为二进制方式: {e}")
        return "binary", {"unit": ""}
    finally:
        if owns and ssh is not None:
            ssh.close()


def _resolve_pid_path(ssh, nginx_bin):
    """解析远程 nginx pid 文件路径"""
    quoted = shlex.quote(nginx_bin)
    ok, ver = _run(ssh, f"{quoted} -V 2>&1")
    if ver:
        match = re.search(r"--pid-path=(\S+)", ver)
        if match:
            return match.group(1).strip()
    if nginx_bin.endswith("/sbin/nginx"):
        prefix = nginx_bin[: -len("/sbin/nginx")]
        return f"{prefix}/logs/nginx.pid"
    return ""


def _is_running_with_ssh(ssh, mode, detail, nginx_bin, log_fn=None):
    """在已连接 SSH 上判断 Nginx 是否在运行"""
    def _log(msg):
        if log_fn:
            log_fn(msg)

    if mode == "systemctl":
        unit = detail.get("unit") or "nginx"
        ok, out = _run(ssh, f"systemctl is-active {unit} 2>/dev/null || true")
        state = (out or "").strip().splitlines()[-1] if out else ""
        running = state in ("active", "activating", "reloading")
        _log(f"Nginx 运行态探测(systemctl {unit}): {state or 'unknown'}")
        return running

    pid_path = _resolve_pid_path(ssh, nginx_bin)
    if pid_path:
        quoted_pid = shlex.quote(pid_path)
        cmd = (
            f'pid=$(cat {quoted_pid} 2>/dev/null | tr -d " \\t\\r\\n"); '
            f'[ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && echo Y'
        )
        ok, out = _run(ssh, cmd)
        if "Y" in (out or ""):
            _log(f"Nginx 运行态探测(pid): 运行中 ({pid_path})")
            return True
        _log(f"Nginx 运行态探测(pid): 未运行 ({pid_path or '无'})")
        return False

    ok, out = _run(ssh, "pgrep -x nginx >/dev/null 2>&1 && echo Y")
    running = "Y" in (out or "")
    _log(f"Nginx 运行态探测(pgrep): {'运行中' if running else '未运行'}")
    return running


def is_nginx_running(
    host,
    port,
    username,
    password=None,
    private_key=None,
    nginx_path=None,
    unit_name=None,
    log_fn=None,
    client=None,
):
    """判断远程 Nginx 是否在运行

    Returns:
        bool: 是否在运行
    """
    nginx_bin = nginx_path or "nginx"
    owns = False
    ssh = None
    try:
        ssh, owns = _open_ssh(
            host, port, username, password=password, private_key=private_key, client=client,
        )
        mode, detail = _detect_mode_with_ssh(
            ssh, _unit_candidates(unit_name), log_fn=log_fn,
        )
        return _is_running_with_ssh(ssh, mode, detail, nginx_bin, log_fn=log_fn)
    except Exception as e:
        if log_fn:
            log_fn(f"探测 Nginx 运行态异常: {e}")
        return False
    finally:
        if owns and ssh is not None:
            ssh.close()


def reload_nginx(
    host,
    port,
    username,
    password=None,
    private_key=None,
    nginx_path=None,
    unit_name=None,
    log_fn=None,
    client=None,
    start_if_stopped=False,
):
    """按启动方式执行 reload：systemctl reload 或 nginx -s reload

    start_if_stopped=True 时，若进程未运行则改为 start（供发布/升级配置生效）。

    Returns:
        tuple: (success: bool, message: str)
    """
    def _log(msg):
        if log_fn:
            log_fn(msg)

    nginx_bin = nginx_path or "nginx"
    owns = False
    ssh = None
    try:
        ssh, owns = _open_ssh(
            host, port, username, password=password, private_key=private_key, client=client,
        )
        mode, detail = _detect_mode_with_ssh(
            ssh, _unit_candidates(unit_name), log_fn=log_fn,
        )

        if start_if_stopped and not _is_running_with_ssh(
            ssh, mode, detail, nginx_bin, log_fn=log_fn,
        ):
            _log("Nginx 未运行，已改为 start")
            if owns and ssh is not None:
                ssh.close()
                owns = False
                ssh = None
            ok, msg = start_nginx(
                host, port, username,
                password=password, private_key=private_key,
                nginx_path=nginx_path, unit_name=unit_name,
                log_fn=log_fn, client=client,
            )
            if not ok:
                return False, msg
            return True, f"未运行，已改为 start: {msg}"

        if mode == "systemctl":
            unit = detail.get("unit") or "nginx"
            cmd = f"systemctl reload {unit} 2>&1"
            _log(f"执行: {cmd}")
            ok, out = _run(ssh, cmd)
            if not ok:
                return False, f"systemctl reload 失败: {out}"
            return True, (out or "").strip() or f"systemctl reload {unit} 成功"

        cmd = f"{nginx_bin} -s reload 2>&1"
        _log(f"执行: {cmd}")
        ok, out = _run(ssh, cmd)
        if not ok:
            return False, f"nginx -s reload 失败: {out}"
        return True, (out or "").strip() or "nginx -s reload 成功"
    except Exception as e:
        return False, str(e)
    finally:
        if owns and ssh is not None:
            ssh.close()


def restart_nginx(
    host,
    port,
    username,
    password=None,
    private_key=None,
    nginx_path=None,
    unit_name=None,
    log_fn=None,
    client=None,
):
    """按启动方式执行 restart：systemctl restart 或 quit 后再启动

    Returns:
        tuple: (success: bool, message: str)
    """
    def _log(msg):
        if log_fn:
            log_fn(msg)

    mode, detail = detect_nginx_manage_mode(
        host, port, username,
        password=password, private_key=private_key,
        unit_name=unit_name, log_fn=log_fn, client=client,
    )
    nginx_bin = nginx_path or "nginx"
    owns = False
    ssh = None
    try:
        ssh, owns = _open_ssh(
            host, port, username, password=password, private_key=private_key, client=client,
        )
        if mode == "systemctl":
            unit = detail.get("unit") or "nginx"
            cmd = f"systemctl restart {unit} 2>&1"
            _log(f"执行: {cmd}")
            ok, out = _run(ssh, cmd)
            if not ok:
                return False, f"systemctl restart 失败: {out}"
            return True, (out or "").strip() or f"systemctl restart {unit} 成功"

        _log(f"执行: {nginx_bin} -s quit")
        ok, out = _run(ssh, f"{nginx_bin} -s quit 2>&1")
        if not ok:
            _log(f"quit 失败，尝试 stop: {out}")
            _run(ssh, f"{nginx_bin} -s stop 2>&1")
        # Nginx 默认 daemon on，主进程自行后台化，无需 nohup/&
        start_cmd = f"{nginx_bin} 2>&1"
        _log(f"执行: {start_cmd}")
        ok, out = _run(ssh, start_cmd)
        if not ok:
            return False, f"二进制重启启动失败: {out}"
        return True, "二进制 quit/start 完成"
    except Exception as e:
        return False, str(e)
    finally:
        if owns and ssh is not None:
            ssh.close()


def stop_nginx(
    host,
    port,
    username,
    password=None,
    private_key=None,
    nginx_path=None,
    unit_name=None,
    log_fn=None,
    client=None,
):
    """按启动方式停止 Nginx

    Returns:
        tuple: (success: bool, message: str)
    """
    def _log(msg):
        if log_fn:
            log_fn(msg)

    mode, detail = detect_nginx_manage_mode(
        host, port, username,
        password=password, private_key=private_key,
        unit_name=unit_name, log_fn=log_fn, client=client,
    )
    nginx_bin = nginx_path or "nginx"
    owns = False
    ssh = None
    try:
        ssh, owns = _open_ssh(
            host, port, username, password=password, private_key=private_key, client=client,
        )
        if mode == "systemctl":
            unit = detail.get("unit") or "nginx"
            cmd = f"systemctl stop {unit} 2>&1"
            _log(f"执行: {cmd}")
            ok, out = _run(ssh, cmd)
            if not ok:
                return False, f"systemctl stop 失败: {out}"
            return True, (out or "").strip() or f"systemctl stop {unit} 成功"

        cmd = f"{nginx_bin} -s quit 2>&1"
        _log(f"执行: {cmd}")
        ok, out = _run(ssh, cmd)
        if not ok:
            ok2, out2 = _run(ssh, f"{nginx_bin} -s stop 2>&1")
            if not ok2:
                return False, f"nginx stop 失败: {out}; {out2}"
            return True, (out2 or "").strip() or "nginx -s stop 成功"
        return True, (out or "").strip() or "nginx -s quit 成功"
    except Exception as e:
        return False, str(e)
    finally:
        if owns and ssh is not None:
            ssh.close()


def start_nginx(
    host,
    port,
    username,
    password=None,
    private_key=None,
    nginx_path=None,
    unit_name=None,
    log_fn=None,
    client=None,
    prefer_mode=None,
    use_sudo=False,
):
    """按启动方式启动 Nginx。

    prefer_mode: None 自动探测；"systemctl" / "binary" 强制指定。
    use_sudo: systemctl 命令是否加 sudo -n（仅 prefer_mode=systemctl 时有意义）。

    Returns:
        tuple: (success: bool, message: str)
    """
    def _log(msg):
        if log_fn:
            log_fn(msg)

    nginx_bin = nginx_path or "nginx"
    unit = (unit_name or DEFAULT_UNIT_NAME).replace(".service", "")
    owns = False
    ssh = None
    try:
        ssh, owns = _open_ssh(
            host, port, username, password=password, private_key=private_key, client=client,
        )

        mode = prefer_mode
        if mode not in ("systemctl", "binary"):
            mode, detail = _detect_mode_with_ssh(
                ssh, _unit_candidates(unit_name), log_fn=log_fn,
            )
            unit = detail.get("unit") or unit

        if mode == "systemctl":
            cmd = _systemd_cmd(f"systemctl start {unit} 2>&1", use_sudo=use_sudo)
            _log(f"执行: {cmd}")
            ok, out = _run(ssh, cmd)
            if not ok:
                return False, f"systemctl start 失败: {out}"
            return True, (out or "").strip() or f"systemctl start {unit} 成功"

        # Nginx 默认 daemon on，主进程自行后台化，无需 nohup/&
        start_cmd = f"{nginx_bin} 2>&1"
        _log(f"执行: {start_cmd}")
        ok, out = _run(ssh, start_cmd)
        if not ok:
            return False, f"二进制启动失败: {out}"
        return True, "二进制启动完成"
    except Exception as e:
        return False, str(e)
    finally:
        if owns and ssh is not None:
            ssh.close()

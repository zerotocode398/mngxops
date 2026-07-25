"""Nginx 启停管理公共方法：检测启动方式并执行 reload/restart/start/stop。"""
from utils.ssh import SSHClient

# 常见 systemd unit 名（按优先级探测）
_DEFAULT_UNITS = ("nginx", "nginx.service")


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


def detect_nginx_manage_mode(
    host,
    port,
    username,
    password=None,
    private_key=None,
    unit_name=None,
    log_fn=None,
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

    auth = _auth_kwargs(password, private_key)
    candidates = []
    if unit_name:
        candidates.append(unit_name.replace(".service", ""))
    for u in _DEFAULT_UNITS:
        name = u.replace(".service", "")
        if name not in candidates:
            candidates.append(name)

    try:
        with SSHClient(host, port, username, **auth) as ssh:
            # 先确认 systemctl 可用
            ok, _ = _run(ssh, "command -v systemctl >/dev/null 2>&1 && echo OK")
            if ok and "OK" in (_ or ""):
                for name in candidates:
                    # active / activating 视为 systemctl 托管
                    ok, out = _run(
                        ssh,
                        f"systemctl is-active {name} 2>/dev/null || true",
                    )
                    state = (out or "").strip().splitlines()[-1] if out else ""
                    if state in ("active", "activating", "reloading"):
                        _log(f"检测到 Nginx 由 systemctl 托管 (unit={name}, state={state})")
                        return "systemctl", {"unit": name, "state": state}
                    # enabled 但未 active 也算托管线索，继续看 is-enabled
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


def reload_nginx(
    host,
    port,
    username,
    password=None,
    private_key=None,
    nginx_path=None,
    unit_name=None,
    log_fn=None,
):
    """按启动方式执行 reload：systemctl reload 或 nginx -s reload

    Returns:
        tuple: (success: bool, message: str)
    """
    def _log(msg):
        if log_fn:
            log_fn(msg)

    mode, detail = detect_nginx_manage_mode(
        host, port, username,
        password=password, private_key=private_key,
        unit_name=unit_name, log_fn=log_fn,
    )
    auth = _auth_kwargs(password, private_key)
    nginx_bin = nginx_path or "nginx"

    try:
        with SSHClient(host, port, username, **auth) as ssh:
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


def restart_nginx(
    host,
    port,
    username,
    password=None,
    private_key=None,
    nginx_path=None,
    unit_name=None,
    log_fn=None,
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
        unit_name=unit_name, log_fn=log_fn,
    )
    auth = _auth_kwargs(password, private_key)
    nginx_bin = nginx_path or "nginx"

    try:
        with SSHClient(host, port, username, **auth) as ssh:
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
            # 后台拉起
            start_cmd = f"nohup {nginx_bin} >/dev/null 2>&1 &"
            _log(f"执行: {start_cmd}")
            ok, out = _run(ssh, start_cmd)
            if not ok:
                return False, f"二进制重启启动失败: {out}"
            return True, "二进制 quit/start 完成"
    except Exception as e:
        return False, str(e)


def stop_nginx(
    host,
    port,
    username,
    password=None,
    private_key=None,
    nginx_path=None,
    unit_name=None,
    log_fn=None,
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
        unit_name=unit_name, log_fn=log_fn,
    )
    auth = _auth_kwargs(password, private_key)
    nginx_bin = nginx_path or "nginx"

    try:
        with SSHClient(host, port, username, **auth) as ssh:
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


def start_nginx(
    host,
    port,
    username,
    password=None,
    private_key=None,
    nginx_path=None,
    unit_name=None,
    log_fn=None,
):
    """按启动方式启动 Nginx

    Returns:
        tuple: (success: bool, message: str)
    """
    def _log(msg):
        if log_fn:
            log_fn(msg)

    mode, detail = detect_nginx_manage_mode(
        host, port, username,
        password=password, private_key=private_key,
        unit_name=unit_name, log_fn=log_fn,
    )
    auth = _auth_kwargs(password, private_key)
    nginx_bin = nginx_path or "nginx"

    try:
        with SSHClient(host, port, username, **auth) as ssh:
            if mode == "systemctl":
                unit = detail.get("unit") or "nginx"
                cmd = f"systemctl start {unit} 2>&1"
                _log(f"执行: {cmd}")
                ok, out = _run(ssh, cmd)
                if not ok:
                    return False, f"systemctl start 失败: {out}"
                return True, (out or "").strip() or f"systemctl start {unit} 成功"

            start_cmd = f"nohup {nginx_bin} >/dev/null 2>&1 &"
            _log(f"执行: {start_cmd}")
            ok, out = _run(ssh, start_cmd)
            if not ok:
                return False, f"二进制启动失败: {out}"
            return True, "二进制启动完成"
    except Exception as e:
        return False, str(e)

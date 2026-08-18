"""OpenSSH 升级：探测、编译、预验证、备份、看门狗回滚、切换、连接实证与手动回滚流水线。

安全模型（失败不丢 SSH）：
1. 预检探测只读，任何失败都不触碰线上 sshd；
2. 新版本先以 DESTDIR 编译到 staging 前缀，用新二进制 `sshd -t` 校验现有配置，
   并在备用端口拉起试连（平台用同凭证真实连接验证）；
3. 切换前完整备份二进制与 /etc/ssh，并生成看门狗回滚脚本；
4. 切换（替换二进制 + 重启 sshd）后，平台用全新连接重连实证；
   失败则在宽限期内由看门狗自动还原旧版本。
"""
import json
import logging
import os
import re
import shlex
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.utils import timezone

from apps.nodes.models import Node
from apps.releases.models import TaskCenterTask
from apps.releases.task_cancel import finish_if_active, is_cancelled, register_ssh, update_if_active
from apps.releases.task_progress import _clear_release_progress_state, _set_current_step
from apps.releases.task_result import build_tree_result, item_failed, item_success, node_header
from utils.setting_service import get_setting
from utils.ssh import SSHClient, upload_file_via_sftp

logger = logging.getLogger(__name__)

_RE_OPENSSH_VERSION = re.compile(r"OpenSSH_([0-9][A-Za-z0-9.\-]*)", re.IGNORECASE)

# 待备份/替换的二进制（按 command -v 解析真实路径）
_BIN_NAMES = (
    "sshd", "ssh", "scp", "sftp", "sftp-server",
    "ssh-keygen", "ssh-keyscan", "ssh-agent", "ssh-add",
)

# 系统级受保护路径（禁止作为备份/安装目标）
_FORBIDDEN_BIN_DIRS = frozenset({"/bin", "/sbin", "/usr", "/etc", "/var", "/lib", "/lib64"})


def batch_max_count():
    """读取批量操作最大节点数"""
    try:
        return max(1, int(get_setting("node.batch_max_count", "3") or 3))
    except (TypeError, ValueError):
        return 3


def default_work_dir():
    """读取默认编译工作目录"""
    return get_setting("openssh.default_work_dir", "/tmp/openssh-upgrade") or "/tmp/openssh-upgrade"


def default_backup_root():
    """读取 OpenSSH 备份根目录"""
    return (
        get_setting("openssh.backup_dir", "/opt/app/mascloud/ansible/mngxops/openssh")
        or "/opt/app/mascloud/ansible/mngxops/openssh"
    )


def default_reconnect_grace():
    try:
        return max(10, int(get_setting("openssh.reconnect_grace_seconds", "60") or 60))
    except (TypeError, ValueError):
        return 60


def default_test_port():
    try:
        return max(0, int(get_setting("openssh.test_port", "2222") or 0))
    except (TypeError, ValueError):
        return 2222


def default_configure_opts():
    return (
        get_setting(
            "openssh.default_configure_opts",
            "--prefix=/usr --sysconfdir=/etc/ssh --with-pam",
        )
        or "--prefix=/usr --sysconfdir=/etc/ssh --with-pam"
    )


def parse_openssh_version(output):
    """从 ssh -V / sshd -V 输出提取 OpenSSH 版本号"""
    m = _RE_OPENSSH_VERSION.search(output or "")
    if m:
        return m.group(1)
    return ""


def _auth_kwargs(credential):
    """按凭证类型组装认证参数"""
    if credential.auth_type == "password":
        return {"password": credential.get_password()}
    return {"private_key": credential.get_private_key()}


def openssh_gate_message(node):
    """返回禁止升级的原因；允许时返回 None。"""
    if node.is_locked:
        return "节点已锁定"
    if node.status != "online":
        return "节点非在线状态"
    cred = node.credential
    if not cred:
        return "未配置凭证"
    if not cred.is_enabled:
        return "凭证已禁用"
    return None


def _priv(cmd, use_sudo=False):
    """按需为命令加 sudo -n 前缀"""
    cmd = (cmd or "").strip()
    if not cmd:
        return cmd
    if use_sudo:
        return f"sudo -n {cmd}"
    return cmd


def _detect_sudo(ssh):
    """探测当前用户是否为 root 或可用免密 sudo。返回 (is_root, use_sudo)"""
    ok, out = ssh.execute_command("id -u 2>/dev/null")
    if ok and (out or "").strip() == "0":
        return True, False
    ok, out = ssh.execute_command("sudo -n true >/dev/null 2>&1 && echo OK")
    if ok and "OK" in (out or ""):
        return False, True
    return False, False


def _detect_sshd_manage_mode(ssh):
    """探测 sshd 服务托管方式。返回 (mode, detail)"""
    units = ("sshd", "ssh")
    ok, _ = ssh.execute_command("command -v systemctl >/dev/null 2>&1 && echo OK")
    if ok and "OK" in (_ or ""):
        for name in units:
            ok, out = ssh.execute_command(
                f"systemctl is-active {name} 2>/dev/null || true"
            )
            state = (out or "").strip().splitlines()[-1] if out else ""
            if state in ("active", "activating", "reloading"):
                return "systemctl", {"unit": name, "state": state}
            ok, en = ssh.execute_command(
                f"systemctl is-enabled {name} 2>/dev/null || true"
            )
            en_state = (en or "").strip().splitlines()[-1] if en else ""
            if en_state in ("enabled", "enabled-runtime", "static"):
                return "systemctl", {"unit": name, "state": state or en_state}
    return "binary", {"unit": ""}


def _detect_package_origin(ssh, binary_path):
    """探测 sshd 二进制是否由 rpm/dpkg 包拥有。返回 dict"""
    result = {"origin": "source", "mgr": "", "package": ""}
    quoted = shlex.quote(binary_path or "")
    if not quoted:
        return result
    ok, out = ssh.execute_command(
        f"rpm -qf --queryformat '%{{NAME}}' {quoted} 2>/dev/null"
    )
    name = (out or "").strip().splitlines()
    name = name[-1].strip() if name else ""
    if ok and name and re.fullmatch(r"[A-Za-z0-9._+-]+", name):
        result.update(origin="package", mgr="rpm", package=name)
        return result
    ok, out = ssh.execute_command(f"dpkg -S {quoted} 2>/dev/null")
    text = (out or "").strip()
    if ok and text:
        first = text.splitlines()[0]
        left = first.split(":", 1)[0].strip()
        pkg = left.split(",")[0].strip()
        if pkg and re.fullmatch(r"[A-Za-z0-9._+-]+", pkg):
            result.update(origin="package", mgr="deb", package=pkg)
    return result


def _resolve_binaries(ssh):
    """解析待替换二进制真实路径。返回 {name: path}"""
    mapping = {}
    names = ", ".join(_BIN_NAMES)
    ok, out = ssh.execute_command(f"for b in {names}; do command -v $b 2>/dev/null; done")
    if ok:
        for name, path in zip(_BIN_NAMES, (out or "").strip().splitlines()):
            path = (path or "").strip()
            if path:
                mapping[name] = path
    return mapping


def _read_port(ssh, config_path):
    """解析 sshd 监听端口（配置 Port 指令，回退 22）"""
    ok, out = ssh.execute_command(
        f"awk '/^[ \\t]*Port/{{print $2}}' {shlex.quote(config_path)} 2>/dev/null | tail -1"
    )
    port = (out or "").strip().splitlines()
    port = port[-1].strip() if port else ""
    if port.isdigit() and 1 <= int(port) <= 65535:
        return int(port)
    return 22


def _probe_one(node):
    """探测单节点 OpenSSH 升级条件（线程内使用主线程已预取字段）"""
    gate = openssh_gate_message(node)
    result = {
        "id": node.id,
        "hostname": node.hostname,
        "ip": node.ip,
        "port": node.port,
        "current_version": node.openssh_version or "",
        "eligible": False,
        "gate_message": gate or "",
        "package_origin": "source",
        "package_mgr": "",
        "package_name": "",
        "manage_mode": "unknown",
        "manage_unit": "",
        "is_root": False,
        "use_sudo": False,
        "home_dir": "",
        "sshd_config_path": "/etc/ssh/sshd_config",
        "sshd_binary": "",
        "sshd_port": 22,
        "binaries": {},
        "disk_free_kb": 0,
        "deps_ok": True,
        "deps_missing": [],
        "warnings": [],
    }
    if gate:
        return result
    cred = node.credential
    if not cred:
        result["gate_message"] = "未配置凭证"
        return result
    try:
        with SSHClient(node.ip, node.port, cred.username, **_auth_kwargs(cred)) as ssh:
            conn = getattr(ssh, "_connect_result", None)
            if isinstance(conn, tuple) and not conn[0]:
                result["gate_message"] = conn[1] or "SSH 连接失败"
                return result
            result["is_root"], result["use_sudo"] = _detect_sudo(ssh)
            if not result["is_root"] and not result["use_sudo"]:
                result["gate_message"] = "需要 root 或免密 sudo 权限"
                return result

            # 主目录（用于回滚脚本存放）
            ok, out = ssh.execute_command("echo $HOME")
            result["home_dir"] = (out or "").strip().splitlines()[-1].strip() if out else ""

            # 版本
            ok, out = ssh.execute_command("sshd -V 2>&1 || ssh -V 2>&1")
            ver = parse_openssh_version(out)
            if ver:
                result["current_version"] = ver

            # 二进制解析
            binaries = _resolve_binaries(ssh)
            result["binaries"] = binaries
            sshd_bin = binaries.get("sshd") or "/usr/sbin/sshd"
            result["sshd_binary"] = sshd_bin

            # 包归属
            pkg = _detect_package_origin(ssh, sshd_bin)
            result["package_origin"] = pkg.get("origin") or "source"
            result["package_mgr"] = pkg.get("mgr") or ""
            result["package_name"] = pkg.get("package") or ""

            # 托管方式
            mode, detail = _detect_sshd_manage_mode(ssh)
            result["manage_mode"] = mode
            result["manage_unit"] = (detail or {}).get("unit") or ""

            # 配置与端口
            ok, out = ssh.execute_command(
                "ls /etc/ssh/sshd_config 2>/dev/null && echo OK"
            )
            if ok and "OK" in (out or ""):
                result["sshd_config_path"] = "/etc/ssh/sshd_config"
            else:
                ok, out = ssh.execute_command(
                    "find /etc/ssh -maxdepth 1 -name 'sshd_config' 2>/dev/null | head -1"
                )
                cfg = (out or "").strip().splitlines()
                result["sshd_config_path"] = cfg[-1].strip() if cfg else "/etc/ssh/sshd_config"
            result["sshd_port"] = _read_port(ssh, result["sshd_config_path"])

            # 磁盘
            work = default_work_dir()
            ok, out = ssh.execute_command(
                f"df -Pk {shlex.quote(work or '/tmp')} 2>/dev/null | tail -1 | awk '{{print $4}}'"
            )
            if ok and (out or "").strip().isdigit():
                result["disk_free_kb"] = int(out.strip())

            # 编译依赖
            missing = []
            for cmd in ("gcc", "make"):
                ok, _ = ssh.execute_command(f"command -v {cmd} >/dev/null 2>&1 && echo OK")
                if not (ok and "OK" in (_ or "")):
                    missing.append(cmd)
            ok, _ = ssh.execute_command("pkg-config --exists zlib 2>/dev/null && echo OK")
            if not (ok and "OK" in (_ or "")):
                missing.append("zlib-devel")
            ok, _ = ssh.execute_command("test -f /usr/include/openssl/opensslv.h && echo OK")
            if not (ok and "OK" in (_ or "")):
                missing.append("openssl-devel")
            ok, _ = ssh.execute_command("test -f /usr/include/security/pam_appl.h && echo OK")
            if not (ok and "OK" in (_ or "")):
                missing.append("pam-devel")
            result["deps_missing"] = missing
            result["deps_ok"] = not missing

            eligible = True
            if result["package_origin"] == "package":
                result["gate_message"] = "检测到包安装（{0}:{1}），请走系统包管理器升级".format(
                    result["package_mgr"], result["package_name"]
                )
                eligible = False
            elif not result["deps_ok"]:
                result["gate_message"] = "缺少编译依赖: " + ", ".join(missing)
                eligible = False
            elif result["disk_free_kb"] and result["disk_free_kb"] < 512 * 1024:
                result["gate_message"] = "磁盘可用空间不足（<512MB）"
                eligible = False
            result["eligible"] = eligible
            if eligible and not ver:
                result["warnings"].append("未能解析当前 OpenSSH 版本")
            return result
    except Exception as exc:
        logger.exception("探测节点 %s OpenSSH 失败", getattr(node, "id", None))
        result["gate_message"] = str(exc) or "探测失败"
        return result


def preview_nodes(node_ids):
    """并行探测选中节点的 OpenSSH 升级条件（上限 batch_max_count）。"""
    if not node_ids:
        return {"success": False, "message": "未选择任何节点"}
    try:
        node_ids = [int(x) for x in node_ids]
    except (TypeError, ValueError):
        return {"success": False, "message": "节点 ID 无效"}

    max_batch = batch_max_count()
    if len(node_ids) > max_batch:
        return {"success": False, "message": f"最多只能操作 {max_batch} 个节点"}

    nodes = list(
        Node.objects.filter(id__in=node_ids, is_deleted=False)
        .select_related("credential")
        .order_by("id")
    )
    if not nodes:
        return {"success": False, "message": "节点不存在"}

    workers = min(max_batch, max(1, len(nodes)))
    by_id = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_probe_one, n): n.id for n in nodes}
        for future in as_completed(futures):
            nid = futures[future]
            try:
                by_id[nid] = future.result()
            except Exception as exc:
                logger.exception("OpenSSH 探测线程异常 node=%s", nid)
                by_id[nid] = {
                    "id": nid,
                    "hostname": "", "ip": "", "port": 22,
                    "current_version": "", "eligible": False,
                    "gate_message": str(exc) or "预览失败",
                    "package_origin": "source", "package_mgr": "", "package_name": "",
                    "manage_mode": "unknown", "manage_unit": "", "is_root": False,
                    "use_sudo": False, "home_dir": "",
                    "sshd_config_path": "/etc/ssh/sshd_config",
                    "sshd_binary": "", "sshd_port": 22, "binaries": {},
                    "disk_free_kb": 0, "deps_ok": True, "deps_missing": [], "warnings": [],
                }

    items = [by_id[n.id] for n in nodes if n.id in by_id]
    return {
        "success": True,
        "nodes": items,
        "defaults": {
            "work_dir": default_work_dir(),
            "test_port": default_test_port(),
            "reconnect_grace_seconds": default_reconnect_grace(),
            "configure_opts": default_configure_opts(),
            "make_jobs": 4,
            "auto_rollback": True,
        },
    }


# ---------------------------------------------------------------------------
# 任务执行
# ---------------------------------------------------------------------------

def _append_log(task, line):
    """追加任务日志并保存"""
    stamp = timezone.now().strftime("%H:%M:%S")
    task.log_output = (task.log_output or "") + f"[{stamp}] {line}\n"
    task.save(update_fields=["log_output", "updated_at"])


def _set_status(task, status, progress=None, step="", error=""):
    """更新单节点任务状态"""
    fields = ["status", "current_step", "updated_at"]
    task.status = status
    task.current_step = step or ""
    if progress is not None:
        task.progress = progress
        fields.append("progress")
    if error:
        task.error_message = error
        fields.append("error_message")
    task.save(update_fields=fields)


def _tcp_ok(ip, port, timeout=3):
    """探测 TCP 端口是否可连接"""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_tcp(ip, port, seconds, step=2):
    """轮询等待 TCP 端口可用；返回是否成功"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if _tcp_ok(ip, port):
            return True
        time.sleep(step)
    return False


def _connect_openssh(node, auth, timeout=10, port_override=None):
    """建立到节点的 SSH 连接并返回 SSHClient（失败抛异常）"""
    port = port_override or node.port
    client = SSHClient(node.ip, port, node.credential.username, **auth)
    ok, msg = client.connect()
    if not ok:
        client.close()
        raise ConnectionError(msg or "SSH 连接失败")
    return client


def _remote_openssh_version(ssh):
    """在已连接会话上读取 sshd 版本"""
    ok, out = ssh.execute_command("sshd -V 2>&1 || ssh -V 2>&1")
    return parse_openssh_version(out) if ok else ""


def _install_staged_binaries(ssh, stage_root, binaries, use_sudo, log):
    """将 staging 前缀下的新二进制安装到系统路径（备份必须先完成）"""
    priv = lambda c: _priv(c, use_sudo=use_sudo)
    sbin = f"{stage_root}/usr/sbin"
    bin_dir = f"{stage_root}/usr/bin"
    libexec = f"{stage_root}/usr/libexec"
    install_map = []

    def _cand(root, name):
        ok, out = ssh.execute_command(
            f"test -f {shlex.quote(root)}/{shlex.quote(name)} && echo OK || echo NO"
        )
        return "OK" in (out or "")

    for name, real_path in binaries.items():
        src = ""
        if _cand(sbin, name):
            src = f"{sbin}/{name}"
        elif _cand(bin_dir, name):
            src = f"{bin_dir}/{name}"
        elif name == "sftp-server" and _cand(libexec, "sftp-server"):
            src = f"{libexec}/sftp-server"
        if src and real_path and real_path != src:
            install_map.append((src, real_path))
    # 兼容 openssh 9.x：sftp-server 在 /usr/lib/openssh 或 stage /usr/libexec
    if "sftp-server" in binaries and not any(
        n == "sftp-server" for n, _ in install_map
    ):
        for cand in (f"{stage_root}/usr/lib/openssh/sftp-server", libexec + "/sftp-server"):
            if _cand("", cand):
                install_map.append((cand, binaries["sftp-server"]))
                break

    for src, dst in install_map:
        log(f"安装: {dst} ← {src}")
        ok, out = ssh.execute_command(
            priv(f"cp -p {shlex.quote(src)} {shlex.quote(dst)} 2>&1")
        )
        if not ok:
            return False, f"安装 {dst} 失败: {out}"
    return True, f"已安装 {len(install_map)} 个二进制"


def _write_rollback_script(task, ssh, use_sudo, log):
    """生成并落盘看门狗回滚脚本，返回 (script_path, ok_marker, rolled_marker)"""
    import os

    manifest = json.loads(task.backup_manifest_json or "{}")
    lines = [
        "#!/bin/sh",
        "# generated by mngxops - OpenSSH upgrade watchdog rollback",
        f': "${{SLEEP:={int(task.reconnect_grace_seconds or 60)}}}"',
        f"OK={shlex.quote(task.ok_marker)}",
        f"ROLLED={shlex.quote(task.rolled_back_marker)}",
        'sleep "$SLEEP"',
        'if [ -f "$OK" ]; then exit 0; fi',
        'if [ -f "$ROLLED" ]; then exit 0; fi',
    ]
    for dst, bak in manifest.items():
        lines.append(f"cp -p {shlex.quote(bak)} {shlex.quote(dst)} 2>/dev/null")
    lines.append(
        f"cp -a {shlex.quote(os.path.join(task.backup_dir, 'etc_ssh'))}/. "
        f"{shlex.quote('/etc/ssh')}/ 2>/dev/null"
    )
    if task.manage_mode == "systemctl":
        unit = task.manage_unit or "sshd"
        lines.append(f"systemctl restart {shlex.quote(unit)} 2>/dev/null || true")
    else:
        lines.append("pkill -x sshd 2>/dev/null || true")
        lines.append("sleep 1")
        lines.append("/usr/sbin/sshd -t 2>/dev/null && /usr/sbin/sshd")
    lines.append(f"touch {shlex.quote(task.rolled_back_marker)}")
    lines.append("exit 0")

    script = "\n".join(lines) + "\n"
    base_dir = "/root/.mngxops" if task.is_root else f"{task.home_dir}/.mngxops"
    mk = _priv(f"mkdir -p {shlex.quote(base_dir)}", use_sudo=use_sudo)
    ssh.execute_command(mk)
    script_path = f"{base_dir}/openssh-rollback-{task.batch_number}.sh"
    content = script.replace("\\", "\\\\").replace("'", "'\\''")
    write_cmd = _priv(
        f"cat > {shlex.quote(script_path)} <<'MNGXOPS_OSHEOF'\n{content}\nMNGXOPS_OSHEOF",
        use_sudo=use_sudo,
    )
    ok, out = ssh.execute_command(write_cmd)
    if not ok:
        return "", task.ok_marker, task.rolled_back_marker
    ok, out = ssh.execute_command(
        _priv(f"chmod +x {shlex.quote(script_path)}", use_sudo=use_sudo)
    )
    if not ok:
        return "", task.ok_marker, task.rolled_back_marker
    log(f"已生成回滚脚本: {script_path}")
    return script_path, task.ok_marker, task.rolled_back_marker


def _schedule_watchdog(ssh, script_path, use_sudo, log):
    """调度看门狗（nohup setsid 优先，at 可用则备用说明）"""
    priv = lambda c: _priv(c, use_sudo=use_sudo)
    cmd = f"nohup setsid sh {shlex.quote(script_path)} >/dev/null 2>&1 &"
    ok, out = ssh.execute_command(priv(cmd))
    if not ok:
        return False, out or "调度看门狗失败"
    log("看门狗已调度（nohup setsid）")
    return True, ""


def _run_switch_and_restart(task, ssh, stage_root, binaries, use_sudo, log):
    """替换二进制并重启 sshd。返回 (ok, msg)"""
    priv = lambda c: _priv(c, use_sudo=use_sudo)
    ok, msg = _install_staged_binaries(ssh, stage_root, binaries, use_sudo, log)
    if not ok:
        return False, msg
    # 特权分离目录
    privsep = "/var/empty/sshd"
    ok, out = ssh.execute_command(
        priv(f"install -d -m 755 {shlex.quote(privsep)} 2>&1")
    )
    if not ok:
        return False, f"创建特权分离目录失败: {out}"

    if task.manage_mode == "systemctl":
        unit = task.manage_unit or "sshd"
        ok, out = ssh.execute_command(priv(f"systemctl restart {unit} 2>&1"))
        if ok:
            log(f"systemctl restart {unit} 完成")
            return True, "systemctl restart 完成"
        # systemctl 失败回退二进制方式
        log(f"systemctl 重启失败，回退二进制方式: {out}")
    ok, out = ssh.execute_command(priv("pkill -x sshd 2>&1 || true"))
    time.sleep(1)
    ok, out = ssh.execute_command(priv("/usr/sbin/sshd -t 2>&1"))
    if not ok:
        return False, f"新 sshd 配置校验失败: {out}"
    ok, out = ssh.execute_command(priv("/usr/sbin/sshd 2>&1"))
    if not ok:
        return False, f"启动新 sshd 失败: {out}"
    log("二进制方式重启 sshd 完成")
    return True, "二进制重启完成"


def _confirm_or_wait_rollback(task, auth, log):
    """切换后连接实证。返回 (ok, version, message)"""
    grace = int(task.reconnect_grace_seconds or 60)
    ok_tcp = _wait_tcp(task.node.ip, task.node.port, grace + 15)
    if ok_tcp:
        try:
            ssh = _connect_openssh(task.node, auth, timeout=10)
            try:
                ver = _remote_openssh_version(ssh)
                # 写 OK 标记解除看门狗
                mk = _priv(
                    f"touch {shlex.quote(task.ok_marker)}", use_sudo=task.use_sudo
                )
                ssh.execute_command(mk)
                return True, ver, "新连接验证成功"
            finally:
                ssh.close()
        except Exception as exc:
            log(f"新连接失败: {exc}，等待看门狗回滚…")
    else:
        log("宽限期内未检测到 sshd 端口，等待看门狗回滚…")

    # 等待回滚完成
    deadline = time.time() + grace + 30
    while time.time() < deadline:
        try:
            ssh = _connect_openssh(task.node, auth, timeout=8)
            try:
                if ssh.execute_command(
                    f"test -f {shlex.quote(task.rolled_back_marker)} && echo Y || echo N"
                )[0]:
                    pass
                ver = _remote_openssh_version(ssh)
                if ver and ver != (task.target_version or ""):
                    return False, ver, "已自动回滚到旧版本"
                return False, ver, "升级失败，已自动回滚"
            finally:
                ssh.close()
        except Exception:
            time.sleep(3)
    return False, "", "升级失败，无法确认 sshd 状态，请人工介入"


def _prepare_stage_and_verify(task, ssh, auth, use_sudo, log):
    """上传源码包、编译到 staging 前缀并预验证。返回 (stage_root, error)"""
    work = task.remote_work_dir
    pkg = task.source_package
    tar_name = (pkg.package_file.name or "").split("/")[-1]
    priv = lambda c: _priv(c, use_sudo=use_sudo)

    _set_status(task, "building", 20, step="上传并解压源码包")
    log(f"创建工作目录: {work}")
    ok, out = ssh.execute_command(
        f"mkdir -p {shlex.quote(work)} 2>&1"
    )
    if not ok:
        return "", f"创建工作目录失败: {out}"

    if pkg.package_file:
        try:
            local = pkg.package_file.path
        except Exception:
            local = ""
        if local and os.path.isfile(local):
            ok, msg = upload_file_via_sftp(
                task.node.ip, task.node.port, task.node.credential.username,
                local_path=local,
                remote_path=f"{work.rstrip('/')}/{tar_name}",
                client=ssh.client,
                **auth,
            )
            if not ok:
                return "", f"上传源码包失败: {msg}"
        else:
            return "", "源码包文件不存在（请重新上传）"
    else:
        return "", "未选择源码包"

    log(f"上传完成: {tar_name}")
    _set_status(task, "building", 30, step="解压源码包")
    extract_dir = f"{work}/src"
    ok, out = ssh.execute_command(
        f"rm -rf {shlex.quote(extract_dir)} && mkdir -p {shlex.quote(extract_dir)} && "
        f"tar -xzf {shlex.quote(work + '/' + tar_name)} -C {shlex.quote(extract_dir)} 2>&1"
    )
    if not ok:
        return "", f"解压失败: {out}"
    ok, out = ssh.execute_command(
        f"ls -d {shlex.quote(extract_dir)}/openssh-* 2>/dev/null | head -1"
    )
    src = (out or "").strip().splitlines()
    src_dir = src[-1].strip() if src else ""
    if not src_dir:
        return "", "未找到解压后的 openssh 源码目录"

    stage = f"{work}/stage"
    ok, out = ssh.execute_command(f"rm -rf {shlex.quote(stage)} && mkdir -p {shlex.quote(stage)}")
    if not ok:
        return "", "创建 staging 目录失败"

    # 强制系统布局，附加用户参数（去掉冲突的 --prefix/--sysconfdir）
    base = "--prefix=/usr --sysconfdir=/etc/ssh"
    extra = []
    for tok in shlex.split(task.configure_opts or ""):
        if tok.startswith("--prefix=") or tok.startswith("--sysconfdir="):
            continue
        extra.append(tok)
    opts = " ".join([base] + extra)

    _set_status(task, "building", 40, step="configure + make + make install DESTDIR")
    cfg_cmd = (
        f"cd {shlex.quote(src_dir)} && "
        f"./configure {opts} >/dev/null 2>&1"
    )
    ok, out = ssh.execute_command(cfg_cmd)
    if not ok:
        return "", "configure 失败（详见完整日志）"
    make_cmd = (
        f"cd {shlex.quote(src_dir)} && "
        f"make -j{int(task.make_jobs or 4)} >/dev/null 2>&1"
    )
    ok, out = ssh.execute_command(make_cmd)
    if not ok:
        return "", "make 编译失败（详见完整日志）"
    inst_cmd = (
        f"cd {shlex.quote(src_dir)} && "
        f"make install DESTDIR={shlex.quote(stage)} >/dev/null 2>&1"
    )
    ok, out = ssh.execute_command(inst_cmd)
    if not ok:
        return "", "make install DESTDIR 失败（详见完整日志）"

    # 预验证 1：新二进制配置校验
    _set_status(task, "verifying", 60, step="新二进制 sshd -t 校验")
    staged_sshd = f"{stage}/usr/sbin/sshd"
    ok, out = ssh.execute_command(
        f"test -x {shlex.quote(staged_sshd)} && echo OK || echo NO"
    )
    if "OK" not in (out or ""):
        return "", "staging 未生成 sshd 二进制"
    ld_env = (
        f"LD_LIBRARY_PATH={shlex.quote(stage + '/usr/lib')}:{shlex.quote(stage + '/usr/lib64')}"
    )
    t_cmd = f"{ld_env} {shlex.quote(staged_sshd)} -t -f {shlex.quote(task.sshd_config_path)} 2>&1"
    ok, out = ssh.execute_command(t_cmd)
    if not ok:
        return "", f"新 sshd 配置校验失败: {out}"

    # 预验证 2：备用端口拉起并真实连接
    test_port = int(task.test_port or 0)
    if test_port and 1 <= test_port <= 65535 and test_port != int(task.sshd_port or 22):
        _set_status(task, "verifying", 70, step=f"备用端口 {test_port} 试连")
        test_conf = f"{work}/sshd_test.conf"
        ok, out = ssh.execute_command(
            f"cp {shlex.quote(task.sshd_config_path)} {shlex.quote(test_conf)} && "
            f"printf '\\nPort {int(test_port)}\\n' >> {shlex.quote(test_conf)}"
        )
        if ok:
            launch = (
                f"nohup {ld_env} {shlex.quote(staged_sshd)} -f {shlex.quote(test_conf)} "
                f">{work}/sshd_test.log 2>&1 & echo $!"
            )
            ok, out = ssh.execute_command(priv(launch))
            pid = (out or "").strip().splitlines()
            pid = pid[-1].strip() if pid else ""
            time.sleep(2)
            test_ok = _tcp_ok(task.node.ip, test_port, timeout=5)
            if test_ok:
                try:
                    s = _connect_openssh(task.node, auth, timeout=8, port_override=test_port)
                    s.close()
                    log("备用端口真实连接成功")
                except Exception as exc:
                    test_ok = False
                    log(f"备用端口连接失败: {exc}")
            if pid and pid.isdigit():
                ssh.execute_command(priv(f"kill {pid} 2>/dev/null || true"))
            if not test_ok:
                return "", "备用端口试连失败，拒绝切换"
        else:
            log("生成测试配置失败，跳过备用端口试连")
    else:
        log("未启用备用端口试连（test_port 为 0 或与监听端口相同）")

    _set_status(task, "verifying", 80, step="预验证完成")
    return stage, ""


def _backup_existing(task, ssh, use_sudo, log):
    """备份既有二进制与 /etc/ssh 目录。返回 (backup_dir, manifest, error)"""
    import os

    timestamp = time.strftime("%Y%m%d%H%M%S")
    host = (task.node.hostname or task.node.ip or "unknown")
    safe_host = "".join(c if c.isalnum() or c in "-_." else "_" for c in host).strip("._") or "unknown"
    backup_root = default_backup_root().rstrip("/")
    backup_dir = f"{backup_root}/{safe_host}/openssh/{timestamp}"
    priv = lambda c: _priv(c, use_sudo=use_sudo)

    _set_status(task, "backing_up", 85, step="备份旧版本")
    ok, out = ssh.execute_command(
        priv(f"mkdir -p {shlex.quote(backup_dir + '/bin')} 2>&1")
    )
    if not ok:
        return "", {}, f"创建备份目录失败: {out}"

    manifest = {}
    for name, path in task.binaries.items():
        if not path:
            continue
        fname = path.replace("/", "_").lstrip("_") or name
        bak = f"{backup_dir}/bin/{fname}"
        ok, out = ssh.execute_command(
            priv(f"cp -p {shlex.quote(path)} {shlex.quote(bak)} 2>&1")
        )
        if not ok:
            return "", {}, f"备份 {path} 失败: {out}"
        manifest[path] = bak
        log(f"备份: {path}")

    ok, out = ssh.execute_command(
        priv(f"cp -a {shlex.quote('/etc/ssh')} {shlex.quote(backup_dir + '/etc_ssh')} 2>&1")
    )
    if not ok:
        return "", {}, f"备份 /etc/ssh 失败: {out}"
    log(f"备份 /etc/ssh → {backup_dir}/etc_ssh")
    return backup_dir, manifest, ""


def _run_one_upgrade(task, tc_id):
    """执行单节点 OpenSSH 升级。返回 (ok, version, message)"""
    node = task.node
    cred = node.credential
    if not cred or not cred.is_enabled:
        return False, "", "凭证不可用"
    auth = _auth_kwargs(cred)

    def log(msg):
        _append_log(task, msg)

    use_sudo = task.use_sudo
    try:
        ssh = _connect_openssh(task, auth, timeout=10)
    except Exception as exc:
        return False, "", f"SSH 连接失败: {exc}"
    register_ssh(tc_id, ssh)
    try:
        if is_cancelled(tc_id):
            return False, "", "任务已取消"
        # 重新权威探测（校验门禁）
        gate = openssh_gate_message(node)
        if gate:
            return False, "", gate
        is_root, cur_sudo = _detect_sudo(ssh)
        use_sudo = cur_sudo or use_sudo
        binaries = _resolve_binaries(ssh)
        if not binaries.get("sshd"):
            return False, "", "无法解析 sshd 二进制路径"
        task.binaries = binaries
        task.is_root = is_root
        task.use_sudo = use_sudo
        ok_home, out_home = ssh.execute_command("echo $HOME")
        task.home_dir = (out_home or "").strip().splitlines()[-1].strip() if out_home else ""
        mode, detail = _detect_sshd_manage_mode(ssh)
        task.manage_mode = mode
        task.manage_unit = (detail or {}).get("unit") or ""
        task.sshd_config_path = task.sshd_config_path or "/etc/ssh/sshd_config"
        task.sshd_port = task.sshd_port or _read_port(ssh, task.sshd_config_path)
        task.save(update_fields=[
            "binaries", "is_root", "use_sudo", "home_dir", "manage_mode", "manage_unit",
            "sshd_config_path", "sshd_port", "updated_at",
        ])

        pkg = _detect_package_origin(ssh, binaries["sshd"])
        if pkg.get("origin") == "package":
            return False, "", f"包安装（{pkg.get('mgr')}:{pkg.get('package')}），请走系统包管理器"

        # 编译到 staging 并预验证
        stage, err = _prepare_stage_and_verify(task, ssh, auth, use_sudo, log)
        if err:
            return False, "", err

        # 备份
        backup_dir, manifest, err = _backup_existing(task, ssh, use_sudo, log)
        if err:
            return False, "", err
        task.backup_dir = backup_dir
        task.backup_manifest_json = json.dumps(manifest, ensure_ascii=False)
        marker_base = f"{default_backup_root().rstrip('/')}/{task.batch_number}"
        task.ok_marker = f"{marker_base}.ok"
        task.rolled_back_marker = f"{marker_base}.rolled"
        task.save(update_fields=[
            "backup_dir", "backup_manifest_json", "ok_marker",
            "rolled_back_marker", "updated_at",
        ])

        # 看门狗脚本
        script_path, _, _ = _write_rollback_script(task, ssh, use_sudo, log)
        if not script_path:
            return False, "", "生成回滚脚本失败（已中止切换，旧版本未受影响）"
        task.rollback_script_path = script_path
        task.save(update_fields=["rollback_script_path", "updated_at"])

        # 调度看门狗
        ok, err = _schedule_watchdog(ssh, script_path, use_sudo, log)
        if not ok:
            return False, "", f"{err}（已中止切换，旧版本未受影响）"

        # 切换并重启
        _set_status(task, "switching", 90, step="替换二进制并重启 sshd")
        ok, msg = _run_switch_and_restart(task, ssh, stage, binaries, use_sudo, log)
        if not ok:
            # 切换失败：立即触发看门狗回滚（SLEEP=0），随后等待回滚完成
            log(f"切换失败: {msg}，立即触发看门狗回滚…")
            ssh.execute_command(_priv(
                f"SLEEP=0 sh {shlex.quote(script_path)} >/dev/null 2>&1 &",
                use_sudo=use_sudo,
            ))
            _confirm_or_wait_rollback(task, auth, log)
            return False, "", f"切换失败，已自动回滚: {msg}"
        log("切换完成，开始连接实证…")

        # 连接实证
        _set_status(task, "confirming", 95, step="连接实证")
        ok, ver, msg = _confirm_or_wait_rollback(task, auth, log)
        return ok, ver, msg
    except Exception as exc:
        logger.exception("OpenSSH 升级执行异常 node=%s", node.id)
        return False, "", f"执行异常: {exc}"
    finally:
        try:
            ssh.close()
        except Exception:
            pass


def _is_ok_marker_written(ssh, task):
    """检查成功标记是否已写入（供线程内判断看门狗是否已解除）"""
    ok, out = ssh.execute_command(
        f"test -f {shlex.quote(task.ok_marker)} && echo Y || echo N"
    )
    return "Y" in (out or "")


def _run_one_rollback(task, tc_id):
    """执行单节点 OpenSSH 手动回滚（恢复备份）。返回 (ok, version, message)"""
    node = task.node
    cred = node.credential
    if not cred or not cred.is_enabled:
        return False, "", "凭证不可用"
    auth = _auth_kwargs(cred)

    def log(msg):
        _append_log(task, msg)

    try:
        ssh = _connect_openssh(task, auth, timeout=10)
    except Exception as exc:
        return False, "", f"SSH 连接失败: {exc}"
    register_ssh(tc_id, ssh)
    try:
        if is_cancelled(tc_id):
            return False, "", "任务已取消"
        use_sudo = task.use_sudo
        is_root, cur_sudo = _detect_sudo(ssh)
        use_sudo = cur_sudo or use_sudo
        task.use_sudo = use_sudo
        task.save(update_fields=["use_sudo", "updated_at"])
        priv = lambda c: _priv(c, use_sudo=use_sudo)

        manifest = json.loads(task.backup_manifest_json or "{}")
        if not manifest:
            return False, "", "备份清单为空，无法回滚"

        _set_status(task, "backing_up", 40, step="停止 sshd")
        ok, out = ssh.execute_command(priv("pkill -x sshd 2>&1 || true"))
        time.sleep(1)

        _set_status(task, "backing_up", 60, step="恢复备份")
        for dst, bak in manifest.items():
            ok, out = ssh.execute_command(
                priv(f"cp -p {shlex.quote(bak)} {shlex.quote(dst)} 2>&1")
            )
            if not ok:
                return False, "", f"恢复 {dst} 失败: {out}"
            log(f"恢复: {dst}")

        import os

        etc_bak = os.path.join(task.backup_dir, "etc_ssh")
        ok, out = ssh.execute_command(
            priv(f"cp -a {shlex.quote(etc_bak)}/. {shlex.quote('/etc/ssh')}/ 2>&1")
        )
        if not ok:
            return False, "", f"恢复 /etc/ssh 失败: {out}"

        _set_status(task, "switching", 80, step="重启 sshd")
        if task.manage_mode == "systemctl":
            unit = task.manage_unit or "sshd"
            ok, out = ssh.execute_command(priv(f"systemctl restart {unit} 2>&1"))
            if not ok:
                return False, "", f"systemctl restart 失败: {out}"
        else:
            ok, out = ssh.execute_command(priv("/usr/sbin/sshd -t 2>&1"))
            if not ok:
                return False, "", f"sshd -t 失败: {out}"
            ok, out = ssh.execute_command(priv("/usr/sbin/sshd 2>&1"))
            if not ok:
                return False, "", f"启动 sshd 失败: {out}"

        _set_status(task, "confirming", 90, step="连接实证")
        ok_tcp = _wait_tcp(node.ip, node.port, 30)
        if not ok_tcp:
            return False, "", "回滚后 sshd 端口未恢复，请人工介入"
        try:
            s = _connect_openssh(task, auth, timeout=10)
            try:
                ver = _remote_openssh_version(s)
            finally:
                s.close()
        except Exception as exc:
            return False, "", f"回滚后连接失败: {exc}"
        return True, ver, "回滚成功，已恢复旧版本"
    except Exception as exc:
        logger.exception("OpenSSH 回滚执行异常 node=%s", node.id)
        return False, "", f"执行异常: {exc}"
    finally:
        try:
            ssh.close()
        except Exception:
            pass


def _finalize_node_tc(node, task, ok, version, message):
    """更新任务终态与节点 OpenSSH 版本"""
    from apps.nodes.services import apply_openssh_probe_result

    now = timezone.now()
    if ok:
        _set_status(task, "success", progress=100, step="升级/回滚成功")
        if task.action == "upgrade":
            task.upgraded_openssh_version = version or ""
        task.error_message = ""
        task.finished_at = now
        task.save(update_fields=[
            "status", "progress", "current_step", "error_message",
            "upgraded_openssh_version", "finished_at", "updated_at",
        ])
        apply_openssh_probe_result(node, True, version=version or "")
        node.save(
            update_fields=["openssh_version", "last_openssh_probe_at", "updated_at"]
        )
    else:
        terminal = "rolled_back" if "回滚" in (message or "") else "failed"
        _set_status(task, terminal, progress=100, step="失败", error=message or "失败")
        task.finished_at = now
        task.save(update_fields=[
            "status", "progress", "current_step", "error_message",
            "finished_at", "updated_at",
        ])
    return ok


def _run_batch(task_center_id, task_ids, action):
    """后台执行批次内各节点任务（并行，上限 batch_max_count）。"""
    from .models import OpenSSHUpgradeTask

    TaskCenterTask.objects.filter(pk=task_center_id).update(
        status="running",
        progress=5,
        detail="正在执行 OpenSSH 升级…" if action == "upgrade" else "正在执行 OpenSSH 回滚…",
        started_at=timezone.now(),
    )
    tasks = list(
        OpenSSHUpgradeTask.objects.filter(id__in=task_ids)
        .select_related("node", "node__credential", "operator", "source_package")
        .order_by("id")
    )
    total = len(tasks)
    success_count = 0
    fail_count = 0
    node_blocks = []

    def _worker(t):
        try:
            if action == "upgrade":
                return _run_one_upgrade(t, task_center_id), t
            return _run_one_rollback(t, task_center_id), t
        except Exception as exc:
            logger.exception("OpenSSH 任务线程异常 task=%s", t.id)
            return (False, "", str(exc)), t

    try:
        workers = min(batch_max_count(), max(1, len(tasks)))
        if workers == 1 or len(tasks) <= 1:
            for t in tasks:
                if is_cancelled(task_center_id):
                    _set_status(t, "cancelled", progress=100, step="已取消")
                    fail_count += 1
                    node_blocks.append(
                        node_header(t.node.ip, t.node.hostname)
                        + "\n" + item_failed("已取消")
                    )
                    continue
                _set_current_step(task_center_id, t.node.hostname, "执行中…")
                (ok, ver, msg), tt = _worker(t)
                _finalize_node_tc(tt.node, tt, ok, ver, msg)
                _set_current_step(task_center_id, tt.node.hostname, None)
                if ok:
                    success_count += 1
                    node_blocks.append(
                        node_header(tt.node.ip, tt.node.hostname)
                        + "\n" + item_success(
                            f"{tt.get_action_display()} {tt.target_version or ''}"
                            .strip() or "成功"
                        )
                    )
                else:
                    fail_count += 1
                    node_blocks.append(
                        node_header(tt.node.ip, tt.node.hostname)
                        + "\n" + item_failed(tt.get_action_display(), msg)
                    )
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_worker, t): t.id for t in tasks}
                for future in as_completed(futures):
                    tid = futures[future]
                    t = next((x for x in tasks if x.id == tid), None)
                    if t is None:
                        continue
                    try:
                        (ok, ver, msg), tt = future.result()
                    except Exception as exc:
                        ok, ver, msg = False, "", str(exc)
                        tt = t
                    _finalize_node_tc(tt.node, tt, ok, ver, msg)
                    _set_current_step(task_center_id, tt.node.hostname, None)
                    if ok:
                        success_count += 1
                        node_blocks.append(
                            node_header(tt.node.ip, tt.node.hostname)
                            + "\n" + item_success(
                                f"{tt.get_action_display()} {tt.target_version or ''}"
                                .strip() or "成功"
                            )
                        )
                    else:
                        fail_count += 1
                        node_blocks.append(
                            node_header(tt.node.ip, tt.node.hostname)
                            + "\n" + item_failed(tt.get_action_display(), msg)
                        )
    except Exception as exc:
        logger.exception("OpenSSH 批次执行异常")
        for t in tasks:
            if t.status in ("pending", "probing", "building", "verifying"):
                _set_status(t, "failed", error=str(exc), progress=100)
        fail_count = max(fail_count, 1)

    result = build_tree_result(success_count, fail_count, total, node_blocks)
    detail = f"OpenSSH {'升级' if action == 'upgrade' else '回滚'}完成：成功 {success_count}，失败 {fail_count}，共 {total}"
    finish_if_active(task_center_id, status="success" if fail_count == 0 else "failed", progress=100, detail=detail, result=result, finished_at=timezone.now())
    _clear_release_progress_state(task_center_id)


def create_batch_from_data(user, data, action="upgrade"):
    """校验并创建 OpenSSH 升级/回滚批次，启动后台线程。

    Returns:
        dict: API 响应字段
    """
    from apps.audit.utils import log_task_center_created

    from .models import (
        OpenSSHUpgradeTask,
        generate_openssh_rollback_batch_number,
        generate_openssh_upgrade_batch_number,
    )

    items = data.get("nodes") or []
    if not isinstance(items, list) or not items:
        return {"success": False, "message": "请选择至少一个节点"}

    max_batch = batch_max_count()
    if len(items) > max_batch:
        return {"success": False, "message": f"最多只能操作 {max_batch} 个节点"}

    prepared = []
    rejected = []
    if action == "upgrade":
        package_id = data.get("package_id")
        package = None
        if package_id:
            try:
                from .models import OpenSSHSourcePackage

                package = OpenSSHSourcePackage.objects.filter(pk=int(package_id)).first()
            except (TypeError, ValueError):
                package = None
        if not package:
            return {"success": False, "message": "请选择 OpenSSH 源码包"}

        work_dir = (data.get("work_dir") or "").strip() or default_work_dir()
        test_port = int(data.get("test_port", default_test_port()) or 0)
        grace = int(data.get("reconnect_grace_seconds", default_reconnect_grace()) or 60)
        configure_opts = (data.get("configure_opts") or "").strip() or default_configure_opts()
        make_jobs = int(data.get("make_jobs", 4) or 4)
        auto_rollback = bool(data.get("auto_rollback", True))

        for item in items:
            try:
                nid = int(item.get("id") or item.get("node_id"))
            except (TypeError, ValueError, AttributeError):
                rejected.append("无效节点 ID")
                continue
            try:
                node = Node.objects.select_related("credential").get(id=nid, is_deleted=False)
            except Node.DoesNotExist:
                rejected.append(f"节点 {nid} 不存在")
                continue
            gate = openssh_gate_message(node)
            if gate:
                rejected.append(f"{node.hostname}（{gate}）")
                continue
            probe = item.get("probe") or {}
            prepared.append({
                "node": node,
                "probe": probe,
                "work_dir": work_dir,
                "test_port": test_port,
                "grace": max(10, grace),
                "configure_opts": configure_opts,
                "make_jobs": max(1, make_jobs),
                "auto_rollback": auto_rollback,
            })
    else:
        # 回滚：items 为源任务 id 列表
        src_ids = []
        for item in items:
            try:
                src_ids.append(int(item.get("id") or item))
            except (TypeError, ValueError, AttributeError):
                rejected.append("无效任务 ID")
        if src_ids:
            sources = OpenSSHUpgradeTask.objects.filter(
                id__in=src_ids, action="upgrade"
            ).select_related("node", "node__credential")
            for src in sources:
                if not src.backup_dir or not src.backup_manifest_json or src.backup_manifest_json == "{}":
                    rejected.append(f"{src.node.hostname}（无备份清单，不可回滚）")
                    continue
                prepared.append({"source": src})
            rejected_ids = set(src_ids) - {s.id for s in sources}
            if rejected_ids:
                rejected.append("任务不存在: " + ",".join(str(x) for x in sorted(rejected_ids)))

    if not prepared:
        msg = "没有可执行的任务"
        if rejected:
            msg += "：" + "；".join(rejected[:5])
        return {"success": False, "message": msg, "skipped": rejected}

    if action == "upgrade":
        batch_number = generate_openssh_upgrade_batch_number()
        act_label = "OpenSSH 升级"
    else:
        batch_number = generate_openssh_rollback_batch_number()
        act_label = "OpenSSH 回滚"

    hostnames = ",".join(
        (p["node"].hostname if action == "upgrade" else p["source"].node.hostname)
        for p in prepared
    )
    ips = ",".join(
        (p["node"].ip if action == "upgrade" else p["source"].node.ip)
        for p in prepared
    )
    tc = TaskCenterTask.objects.create(
        operation_type="openssh_upgrade",
        status="pending",
        detail=f"任务已创建，等待执行 {act_label}",
        target_hostnames=hostnames,
        target_ips=ips,
        target_configs=act_label,
        source_batch=batch_number,
        trigger_user=user,
    )
    log_task_center_created(tc, user=user)

    task_ids = []
    for p in prepared:
        if action == "upgrade":
            node = p["node"]
            probe = p["probe"]
            binaries = probe.get("binaries") or {}
            if isinstance(binaries, str):
                try:
                    binaries = json.loads(binaries or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    binaries = {}
            t = OpenSSHUpgradeTask.objects.create(
                action="upgrade",
                batch_number=batch_number,
                node=node,
                source_package=package,
                current_version=(probe.get("current_version") or node.openssh_version or ""),
                target_version=package.version,
                configure_opts=p["configure_opts"],
                target_prefix="/usr",
                make_jobs=p["make_jobs"],
                remote_work_dir=p["work_dir"],
                test_port=p["test_port"],
                reconnect_grace_seconds=p["grace"],
                auto_rollback=p["auto_rollback"],
                ssd_binary=probe.get("sshd_binary") or "",
                ssd_config_path=probe.get("sshd_config_path") or "/etc/ssh/sshd_config",
                ssd_port=probe.get("sshd_port") or 22,
                manage_mode=probe.get("manage_mode") or "binary",
                manage_unit=probe.get("manage_unit") or "",
                use_sudo=bool(probe.get("use_sudo")),
                binaries=binaries,
                task_center=tc,
                operator=user,
            )
        else:
            src = p["source"]
            t = OpenSSHUpgradeTask.objects.create(
                action="rollback",
                batch_number=batch_number,
                node=src.node,
                source_package=None,
                current_version=src.current_version or "",
                target_version=src.target_version or "",
                configure_opts="",
                target_prefix="",
                remote_work_dir=src.remote_work_dir,
                test_port=0,
                reconnect_grace_seconds=src.reconnect_grace_seconds,
                auto_rollback=False,
                backup_dir=src.backup_dir,
                backup_manifest_json=src.backup_manifest_json,
                manage_mode=src.manage_mode,
                manage_unit=src.manage_unit,
                use_sudo=src.use_sudo,
                sshd_binary=src.sshd_binary,
                sshd_config_path=src.sshd_config_path,
                sshd_port=src.sshd_port,
                binaries=src.binaries,
                task_center=tc,
                operator=user,
            )
        task_ids.append(t.id)

    thread = threading.Thread(
        target=_run_batch,
        args=(tc.id, task_ids, action),
        daemon=True,
    )
    thread.start()

    message = f"已创建 {act_label} 任务（{len(prepared)} 台）"
    if rejected:
        message += f"；已跳过 {len(rejected)} 台"
    return {
        "success": True,
        "async": True,
        "message": message,
        "task_center_id": tc.id,
        "source_batch": batch_number,
        "skipped": rejected,
    }


def create_rollback_batch_from_data(user, data):
    """创建手动回滚批次（items 为源 OpenSSHUpgradeTask id 列表）"""
    return create_batch_from_data(user, data, action="rollback")
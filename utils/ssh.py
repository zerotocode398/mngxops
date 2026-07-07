import paramiko
from io import StringIO
import time


class SSHClient:
    def __init__(self, host, port, username, password=None, private_key=None):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.private_key = private_key
        self.client = None

    def connect(self):
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            if self.private_key:
                pkey = paramiko.RSAKey.from_private_key(StringIO(self.private_key))
                self.client.connect(
                    hostname=self.host,
                    port=self.port,
                    username=self.username,
                    pkey=pkey,
                    timeout=10,
                )
            else:
                self.client.connect(
                    hostname=self.host,
                    port=self.port,
                    username=self.username,
                    password=self.password,
                    timeout=10,
                )
            return True, "连接成功"
        except Exception as e:
            return False, str(e)

    def execute_command(self, command):
        if not self.client:
            return False, "SSH连接未建立"

        try:
            stdin, stdout, stderr = self.client.exec_command(command)
            output = stdout.read().decode("utf-8")
            error = stderr.read().decode("utf-8")

            if error:
                return False, error
            return True, output
        except Exception as e:
            return False, str(e)

    def close(self):
        if self.client:
            self.client.close()
            self.client = None

    def __enter__(self):
        self._connect_result = self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def test_ssh_connection(host, port, username, password=None, private_key=None):
    try:
        with SSHClient(host, port, username, password, private_key) as ssh:
            return ssh._connect_result
    except Exception as e:
        return False, str(e)


def get_nginx_version(
    host, port, username, password=None, private_key=None, nginx_path=None
):
    try:
        with SSHClient(host, port, username, password, private_key) as ssh:
            if nginx_path:
                command = f"{nginx_path} -v 2>&1"
            else:
                command = "nginx -v 2>&1"

            success, output = ssh.execute_command(command)

            if success and "nginx version" in output.lower():
                version = output.strip()
                if "nginx version:" in version:
                    version = version.split("nginx version:")[-1].strip()
                if version.startswith("nginx/"):
                    version = version.replace("nginx/", "")
                return True, version

            if not success:
                return False, output

            return False, "无法获取nginx版本信息"
    except Exception as e:
        return False, str(e)


def read_remote_file(
    host, port, username, password=None, private_key=None, file_path=None
):
    try:
        with SSHClient(host, port, username, password, private_key) as ssh:
            success, output = ssh.execute_command(f"cat {file_path}")
            if not success:
                return False, f"读取失败: {output}"
            return True, output
    except Exception as e:
        return False, str(e)


def expand_remote_glob(
    host, port, username, password=None, private_key=None, pattern=None
):
    try:
        with SSHClient(host, port, username, password, private_key) as ssh:
            success, output = ssh.execute_command(f"ls {pattern} 2>/dev/null")
            if not success:
                return []
            return [f.strip() for f in output.strip().split("\n") if f.strip()]
    except Exception:
        return []


def discover_nginx_configs(
    host,
    port,
    username,
    password=None,
    private_key=None,
    nginx_conf_path="",
    max_include_depth=3,
):
    import posixpath
    import re

    results = []
    errors = []

    include_pattern = re.compile(r"include\s+([^;]+?)\s*;")
    skip_files = {
        "mime.types",
        "fastcgi_params",
        "fastcgi.conf",
        "uwsgi_params",
        "scgi_params",
        "koi-utf",
        "koi-win",
        "win-utf",
    }

    def is_builtin_file(path: str):
        name = path.split("/")[-1]
        if name in skip_files:
            return True
        if "/modules/" in path:
            return True
        return False

    def normalize_include_path(raw_path: str, current_dir: str) -> str:
        # Support include with quotes and relative segments like ../conf.d/*.conf
        include_path = (raw_path or "").strip().strip("\"'")
        if not include_path:
            return ""
        if include_path.startswith("/"):
            return str(posixpath.normpath(include_path))
        return str(posixpath.normpath(posixpath.join(current_dir, include_path)))

    pending = [(str(nginx_conf_path), 0)]
    seen = set()
    depth_limited = set()

    while pending:
        current_path, current_depth = pending.pop(0)
        if current_path in seen:
            continue
        seen.add(current_path)

        success, content = read_remote_file(
            host, port, username, password, private_key, current_path
        )
        if not success:
            errors.append(f"读取 {current_path} 失败: {content}")
            continue

        current_name = current_path.split("/")[-1]
        if current_path == nginx_conf_path or not is_builtin_file(current_path):
            results.append(
                {
                    "path": current_path,
                    "name": current_name,
                    "content": content,
                }
            )

        current_dir = posixpath.dirname(current_path) or "/"
        for match in include_pattern.finditer(content):
            include_path = normalize_include_path(match.group(1), current_dir)
            if not include_path:
                continue

            if "*" in include_path:
                files = expand_remote_glob(
                    host, port, username, password, private_key, include_path
                )
            else:
                files = [include_path]

            for f_path in files:
                normalized_f_path: str = str(f_path).strip().strip("\"'")
                if not normalized_f_path:
                    continue
                if is_builtin_file(normalized_f_path):
                    continue
                if normalized_f_path not in seen:
                    next_depth = current_depth + 1
                    if next_depth > max_include_depth:
                        if normalized_f_path not in depth_limited:
                            errors.append(
                                f"include 递归超限（>{max_include_depth}）：{normalized_f_path}"
                            )
                            depth_limited.add(normalized_f_path)
                        continue
                    pending.append((str(normalized_f_path), next_depth))

    return results, errors


def get_system_info(host, port, username, password=None, private_key=None):
    try:
        info = {}
        with SSHClient(host, port, username, password, private_key) as ssh:
            success, output = ssh.execute_command(
                "grep PRETTY_NAME /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '\"' || cat /etc/redhat-release 2>/dev/null || uname -s"
            )
            if success and output.strip():
                info["os"] = output.strip()
            else:
                info["os"] = "未知"

            success, output = ssh.execute_command("uname -r | cut -d- -f1")
            if success and output.strip():
                info["kernel"] = output.strip()
            else:
                info["kernel"] = "未知"

            success, output = ssh.execute_command(
                'lscpu | grep "Model name" | cut -d: -f2 | xargs'
            )
            if success and output.strip():
                info["cpu"] = output.strip()
            else:
                success, output = ssh.execute_command(
                    'cat /proc/cpuinfo | grep "model name" | head -1 | cut -d: -f2 | xargs'
                )
                if success and output.strip():
                    info["cpu"] = output.strip()
                else:
                    info["cpu"] = "未知"

            success, output = ssh.execute_command("nproc")
            if success and output.strip():
                info["cpu_cores"] = output.strip()
            else:
                info["cpu_cores"] = "未知"

            success, output = ssh.execute_command(
                "free -h | grep Mem | awk '{print $2}'"
            )
            if success and output.strip():
                info["memory_total"] = output.strip()
            else:
                info["memory_total"] = "未知"

            success, output = ssh.execute_command(
                "free -h | grep Mem | awk '{print $3}'"
            )
            if success and output.strip():
                info["memory_used"] = output.strip()
            else:
                info["memory_used"] = "未知"

            success, output = ssh.execute_command(
                "df -h / | tail -1 | awk '{print $2}'"
            )
            if success and output.strip():
                info["disk_total"] = output.strip()
            else:
                info["disk_total"] = "未知"

            success, output = ssh.execute_command(
                "df -h / | tail -1 | awk '{print $3}'"
            )
            if success and output.strip():
                info["disk_used"] = output.strip()
            else:
                info["disk_used"] = "未知"

            success, output = ssh.execute_command("uptime -p")
            if success and output.strip():
                info["uptime"] = output.strip().replace("up ", "")
            else:
                success, output = ssh.execute_command(
                    "uptime | awk '{print $3,$4}' | sed 's/,//'"
                )
                if success and output.strip():
                    info["uptime"] = output.strip()
                else:
                    info["uptime"] = "未知"

            return True, info
    except Exception as e:
        return False, str(e)


def upload_file_via_sftp(
    host,
    port,
    username,
    password=None,
    private_key=None,
    local_path=None,
    remote_path=None,
    content=None,
):
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        if private_key:
            pkey = paramiko.RSAKey.from_private_key(StringIO(private_key))
            client.connect(
                hostname=host,
                port=port,
                username=username,
                pkey=pkey,
                timeout=10,
            )
        else:
            client.connect(
                hostname=host,
                port=port,
                username=username,
                password=password,
                timeout=10,
            )

        sftp = client.open_sftp()

        if local_path:
            sftp.put(local_path, remote_path)
        elif content is not None:
            import tempfile
            import os

            content = content.replace("\r\n", "\n").replace("\r", "\n")

            temp_file = tempfile.NamedTemporaryFile(
                mode="w", suffix=".tmp", delete=False, encoding="utf-8", newline=""
            )
            temp_file.write(content)
            temp_path = temp_file.name
            temp_file.close()

            try:
                sftp.put(temp_path, remote_path)
            finally:
                os.unlink(temp_path)

        sftp.close()
        client.close()
        return True, "上传成功"
    except Exception as e:
        return False, str(e)


def backup_remote_file(
    host,
    port,
    username,
    password=None,
    private_key=None,
    file_path=None,
    backup_dir="/opt/app/mascloud/ansible/mngxops",
):
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        if private_key:
            pkey = paramiko.RSAKey.from_private_key(StringIO(private_key))
            client.connect(
                hostname=host,
                port=port,
                username=username,
                pkey=pkey,
                timeout=10,
            )
        else:
            client.connect(
                hostname=host,
                port=port,
                username=username,
                password=password,
                timeout=10,
            )

        timestamp = time.strftime("%Y%m%d%H%M%S")
        filename = file_path.split("/")[-1]
        backup_name = f"{filename}.{timestamp}"
        backup_path = f"{backup_dir}/{backup_name}"

        _, stdout, stderr = client.exec_command(f"mkdir -p {backup_dir}")
        mkdir_exit = stdout.channel.recv_exit_status()
        if mkdir_exit != 0:
            err = stderr.read().decode("utf-8").strip()
            client.close()
            return False, f"创建备份目录失败: {err}"

        _, stdout, stderr = client.exec_command(f"cp {file_path} {backup_path}")
        exit_code = stdout.channel.recv_exit_status()

        client.close()

        if exit_code == 0:
            return True, backup_path
        else:
            err = stderr.read().decode("utf-8").strip()
            return False, f"备份失败: {err}"
    except Exception as e:
        return False, str(e)


def restore_backup_file(
    host,
    port,
    username,
    password=None,
    private_key=None,
    backup_path=None,
    original_path=None,
):
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        if private_key:
            pkey = paramiko.RSAKey.from_private_key(StringIO(private_key))
            client.connect(
                hostname=host,
                port=port,
                username=username,
                pkey=pkey,
                timeout=10,
            )
        else:
            client.connect(
                hostname=host,
                port=port,
                username=username,
                password=password,
                timeout=10,
            )

        _, stdout, stderr = client.exec_command(f"cp {backup_path} {original_path}")
        exit_code = stdout.channel.recv_exit_status()

        client.close()

        if exit_code == 0:
            return True, "回滚成功"
        else:
            err = stderr.read().decode("utf-8").strip()
            return False, f"回滚失败: {err}"
    except Exception as e:
        return False, str(e)


def check_remote_file_size(
    host,
    port,
    username,
    password=None,
    private_key=None,
    file_path=None,
):
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        if private_key:
            pkey = paramiko.RSAKey.from_private_key(StringIO(private_key))
            client.connect(
                hostname=host,
                port=port,
                username=username,
                pkey=pkey,
                timeout=10,
            )
        else:
            client.connect(
                hostname=host,
                port=port,
                username=username,
                password=password,
                timeout=10,
            )

        _, stdout, stderr = client.exec_command(f"wc -c < {file_path}")
        out = stdout.read().decode("utf-8").strip()
        exit_code = stdout.channel.recv_exit_status()

        client.close()

        if exit_code == 0:
            size = int(out)
            return size > 0, f"{size} bytes"
        else:
            err = stderr.read().decode("utf-8").strip()
            return False, f"检查失败: {err}"
    except Exception as e:
        return False, str(e)


def copy_remote_file(
    host,
    port,
    username,
    password=None,
    private_key=None,
    src_path=None,
    dst_path=None,
):
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        if private_key:
            pkey = paramiko.RSAKey.from_private_key(StringIO(private_key))
            client.connect(
                hostname=host,
                port=port,
                username=username,
                pkey=pkey,
                timeout=10,
            )
        else:
            client.connect(
                hostname=host,
                port=port,
                username=username,
                password=password,
                timeout=10,
            )

        _, stdout, stderr = client.exec_command(f"cp {src_path} {dst_path}")
        exit_code = stdout.channel.recv_exit_status()

        client.close()

        if exit_code == 0:
            return True, "复制成功"
        else:
            err = stderr.read().decode("utf-8").strip()
            return False, f"复制失败: {err}"
    except Exception as e:
        return False, str(e)


def check_remote_file_md5(
    host,
    port,
    username,
    password=None,
    private_key=None,
    file_path=None,
):
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        if private_key:
            pkey = paramiko.RSAKey.from_private_key(StringIO(private_key))
            client.connect(
                hostname=host,
                port=port,
                username=username,
                pkey=pkey,
                timeout=10,
            )
        else:
            client.connect(
                hostname=host,
                port=port,
                username=username,
                password=password,
                timeout=10,
            )

        _, stdout, stderr = client.exec_command(f"md5sum {file_path}")
        out = stdout.read().decode("utf-8").strip()
        exit_code = stdout.channel.recv_exit_status()

        client.close()

        if exit_code == 0:
            md5 = out.split()[0]
            return True, md5
        else:
            err = stderr.read().decode("utf-8").strip()
            return False, f"md5 失败: {err}"
    except Exception as e:
        return False, str(e)


def execute_nginx_test(
    host,
    port,
    username,
    password=None,
    private_key=None,
    nginx_path=None,
    config_path=None,
):
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        if private_key:
            pkey = paramiko.RSAKey.from_private_key(StringIO(private_key))
            client.connect(
                hostname=host,
                port=port,
                username=username,
                pkey=pkey,
                timeout=10,
            )
        else:
            client.connect(
                hostname=host,
                port=port,
                username=username,
                password=password,
                timeout=10,
            )

        nginx_bin = nginx_path or "nginx"
        command = f"{nginx_bin} -t"

        _, stdout, stderr = client.exec_command(command)
        out = stdout.read().decode("utf-8")
        err = stderr.read().decode("utf-8")
        exit_code = stdout.channel.recv_exit_status()

        client.close()

        combined = out + err
        return exit_code == 0, combined.strip()
    except Exception as e:
        return False, str(e)


def execute_nginx_reload(
    host,
    port,
    username,
    password=None,
    private_key=None,
    nginx_path=None,
):
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        if private_key:
            pkey = paramiko.RSAKey.from_private_key(StringIO(private_key))
            client.connect(
                hostname=host,
                port=port,
                username=username,
                pkey=pkey,
                timeout=10,
            )
        else:
            client.connect(
                hostname=host,
                port=port,
                username=username,
                password=password,
                timeout=10,
            )

        nginx_bin = nginx_path or "nginx"
        command = f"{nginx_bin} -s reload"

        _, stdout, stderr = client.exec_command(command)
        out = stdout.read().decode("utf-8")
        err = stderr.read().decode("utf-8")
        exit_code = stdout.channel.recv_exit_status()

        client.close()

        combined = out + err
        return exit_code == 0, combined.strip() or "reload 成功"
    except Exception as e:
        return False, str(e)

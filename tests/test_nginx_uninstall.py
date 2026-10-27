"""验证 Nginx 卸载的 systemd 状态探测。"""


def test_uninstall_systemd_probe_checks_nginx_service_once(monkeypatch):
    """验证卸载探测不重复查询 nginx 的等价 unit 名称。"""
    from ngxops.nginx_uninstall import services

    commands = []

    def fake_run_command(_client, command, context=None, timeout=45):
        """返回未运行且未启用的 systemd 状态及可管理目录。"""
        commands.append(command)
        if command == "command -v systemctl >/dev/null 2>&1":
            return 0, ""
        if command.startswith("systemctl is-active"):
            return 3, "inactive"
        if command.startswith("systemctl is-enabled"):
            return 1, "disabled"
        if command == "test -w /etc/systemd/system":
            return 0, ""
        raise AssertionError("收到意外探测命令：{}".format(command))

    monkeypatch.setattr(services, "_run_command", fake_run_command)
    result = services._systemd_info(object())

    assert result == {
        "mode": "binary",
        "unit": "",
        "running": False,
        "can_manage": True,
        "use_sudo": False,
    }
    assert commands == [
        "command -v systemctl >/dev/null 2>&1",
        "systemctl is-active nginx.service 2>/dev/null || true",
        "systemctl is-enabled nginx.service 2>/dev/null || true",
        "test -w /etc/systemd/system",
    ]

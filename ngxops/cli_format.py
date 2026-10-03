"""提供 CLI 参数默认值格式化器。"""

import argparse


class NonNullDefaultsHelpFormatter(argparse.ArgumentDefaultsHelpFormatter):
    """显示实际默认值并隐藏未设置的 None 默认值。"""

    def _get_help_string(self, action: argparse.Action) -> str:
        """生成不显示 None 默认值的帮助文本。"""
        if action.default is None:
            return action.help or ""
        return super()._get_help_string(action)

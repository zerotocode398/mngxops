"""统一密码强度提示：用户名相似度 + 最短长度合并为一条中文文案。"""
from django.contrib.auth.password_validation import (
    MinimumLengthValidator,
    UserAttributeSimilarityValidator,
)
from django.core.exceptions import ValidationError


_COMBINED_MESSAGE = "密码不能与用户名过于相似，且长度不能少于 8 个字符。"


class CombinedSimilarityAndLengthValidator:
    """相似度与最短长度任一失败时，返回统一中文提示。"""

    def __init__(self, min_length=8):
        """初始化内置相似度与最短长度校验器。"""
        self.min_length = min_length
        self._similarity = UserAttributeSimilarityValidator()
        self._length = MinimumLengthValidator(min_length=min_length)

    def validate(self, password, user=None):
        """校验密码；失败时只抛出一条合并文案。"""
        failed = False
        for validator in (self._similarity, self._length):
            try:
                validator.validate(password, user)
            except ValidationError:
                failed = True
        if failed:
            raise ValidationError(_COMBINED_MESSAGE, code="password_too_weak")

    def get_help_text(self):
        """返回密码帮助文案。"""
        return _COMBINED_MESSAGE

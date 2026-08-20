"""列表搜索公共工具：将逗号分隔的搜索标签拆成非空列表。"""


def split_search_tags(search):
    """将逗号分隔的搜索标签拆成非空列表（支持中文逗号 ，）。

    用于全站 tag-input 查询的后端分词：标签间 AND、标签内字段 OR。
    """
    if not search:
        return []
    return [t.strip() for t in search.replace("，", ",").split(",") if t.strip()]

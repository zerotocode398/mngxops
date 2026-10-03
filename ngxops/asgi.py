"""导出 Uvicorn 使用的 ASGI 应用对象。"""

from ngxops.app import create_app


app = create_app()
application = app

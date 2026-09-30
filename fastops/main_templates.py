"""FastAPI Jinja 模板实例。"""

from fastapi.templating import Jinja2Templates

from fastops.core.config import get_settings


templates = Jinja2Templates(directory=str(get_settings().template_path))

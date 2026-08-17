"""
WSGI config for ngxops project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/wsgi/
"""

import os
import logging

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ngxops.settings')

application = get_wsgi_application()

try:
    from apps.releases.startup_cleanup import cleanup_stale_running_tasks

    cleanup_stale_running_tasks()
except Exception:
    logging.getLogger(__name__).exception("启动清理遗留任务失败")

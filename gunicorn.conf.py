"""Production process manager: gunicorn + uvicorn workers.

Example:
  gunicorn -c gunicorn.conf.py app.main:app
"""

bind = "0.0.0.0:8000"
worker_class = "uvicorn.workers.UvicornWorker"
workers = 2
timeout = 60
keepalive = 5
accesslog = "-"
errorlog = "-"

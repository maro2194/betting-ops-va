#!/bin/bash
cd /opt/tab-betting-backend
exec uvicorn main:app --host 127.0.0.1 --port 8001 --log-level info

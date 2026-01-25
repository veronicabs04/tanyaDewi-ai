FROM python:3.11-slim

WORKDIR /srv

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
  && rm -rf /var/lib/apt/lists/*

COPY my_agent/app/requirement.txt /srv/my_agent/app/requirement.txt
RUN pip install --no-cache-dir -r /srv/my_agent/app/requirement.txt

COPY my_agent /srv/my_agent

RUN mkdir -p /srv/data


ENV PYTHONPATH=/srv
EXPOSE 8001

CMD ["python","-m","uvicorn","my_agent.app.server:app","--host","0.0.0.0","--port","8001","--proxy-headers","--forwarded-allow-ips","*"]



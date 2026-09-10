FROM python:3.12

WORKDIR /app

# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:$PATH"

# Copy requirements first for better caching
COPY server/requirements.txt .
RUN pip install -r requirements.txt

# Install mem0 in editable mode using Poetry. This lives outside /app
# (not /app/packages) so the compose service's `.:/app` bind mount — which
# maps only the server/ directory — can't shadow it. Only server/ code
# hot-reloads via that mount; mem0/ changes need an image rebuild.
WORKDIR /packages
COPY pyproject.toml .
COPY poetry.lock .
COPY README.md .
COPY mem0 ./mem0
RUN pip install -e .[graph]

# Return to app directory and copy server code
WORKDIR /app
COPY server .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

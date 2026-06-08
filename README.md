# 특정 환경 내에서 사용하기 위한 GPT Researcher 배치 운영 가이드

이 문서는 뉴스 DB와 OpenAI-compatible GPT API를 사용하는 배치 전용 GPT Researcher 운영 방법을 정리합니다.

현재 목표는 프론트/백엔드 서비스 운영이 아니라 cron 배치입니다. 소스의 `frontend/`, `backend/`는 삭제하지 않지만 운영 서버에서는 Next.js, LangGraph, FastAPI 서버를 실행하지 않습니다. `frontend/nextjs/components/Langgraph/Langgraph.js`의 `authToken`도 배치 실행에는 필요 없습니다.

## 1. 처리 흐름

기존 GPT Researcher의 연구 파이프라인은 그대로 사용합니다.

1. cron이 `python -m gpt_researcher.corp.news_batch`를 실행합니다.
2. 배치가 PostgreSQL의 `corp.research_topics`에서 실행 대상 주제를 가져옵니다.
3. GPT Researcher가 주제를 분석해 검색용 하위 질문/쿼리를 여러 개 생성합니다.
4. 검색기는 외부 Tavily가 아니라 `postgres_news` retriever를 사용합니다.
5. `postgres_news`가 PostgreSQL/pgvector 뉴스 테이블에서 관련 기사 본문을 검색합니다.
6. 검색된 기사 `raw_content`가 외부 웹 스크래핑 없이 context 압축/수집 단계로 전달됩니다.
7. GPT chat API가 context 기반 최종 보고서를 작성합니다.
8. 보고서, context, source URL, 비용, 상태, 오류 메시지가 `corp.research_reports`에 저장됩니다.

## 2. 필요한 실행 파일

배치 entrypoint는 다음 모듈입니다.

```bash
python -m gpt_researcher.corp.news_batch
```

주요 코드 위치는 다음과 같습니다.

```text
gpt_researcher/corp/db.py          # 주제 claim, 실행 row 생성, 성공/실패 저장
gpt_researcher/corp/news_batch.py  # cron용 batch runner
gpt_researcher/retrievers/postgres_news/
```

운영에서 실행하지 않는 항목은 다음과 같습니다.

```text
frontend/nextjs       # Next.js UI 불필요
backend/              # API 서버 불필요
LangGraph 서버         # 배치에는 authToken 불필요
Tavily API             # 사용하지 않음
```

## 3. 리눅스 서버 설치

프로젝트를 서버에 복사한 뒤 프로젝트 루트에서 실행합니다.

```bash
cd /opt/gpt-researcher

python3.11 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

서버가 외부 인터넷에 접근할 수 없다면 인터넷 가능한 PC에서 wheel 파일을 먼저 받습니다.

```bash
pip download -r requirements.txt -d wheelhouse
```

`wheelhouse/`를 서버로 옮긴 뒤 설치합니다.

```bash
cd /opt/gpt-researcher
source .venv/bin/activate

pip install --no-index --find-links wheelhouse -r requirements.txt
pip install -e .
```

PostgreSQL/pgvector 연동에 필요한 핵심 Python 패키지는 이미 requirements에 포함되어 있습니다.

```text
psycopg[binary,pool]
pgvector
python-dotenv
```

## 4. 환경 변수

프로젝트 루트에 `.env` 파일을 만듭니다. `news_batch`는 실행 시 `.env`를 자동으로 로드합니다.

```env
# Batch topic/result DB
CORP_BATCH_DSN="host=10.x.x.x port=5432 dbname=research user=gptr_writer password=PASSWORD sslmode=require"
CORP_BATCH_SCHEMA=corp
CORP_BATCH_LIMIT=10
CORP_BATCH_LOCK_TIMEOUT_SECONDS=0
CORP_BATCH_CONNECT_TIMEOUT=10

# Retriever
RETRIEVER=postgres_news
SCRAPER=bs

# Internal OpenAI-compatible API
OPENAI_BASE_URL=https://internal-gpt-api.example.com/v1
OPENAI_API_KEY=YOUR_INTERNAL_API_KEY
FAST_LLM=openai:YOUR_CHAT_MODEL
SMART_LLM=openai:YOUR_CHAT_MODEL
STRATEGIC_LLM=openai:YOUR_CHAT_MODEL
EMBEDDING=openai:YOUR_EMBEDDING_MODEL

# News PostgreSQL / pgvector
POSTGRES_NEWS_DSN="host=10.x.x.x port=5432 dbname=news user=gptr_reader password=PASSWORD sslmode=require"
POSTGRES_NEWS_ARTICLES_TABLE=news.articles
POSTGRES_NEWS_EMBEDDINGS_TABLE=news.article_embeddings
POSTGRES_NEWS_ARTICLE_ID_COLUMN=id
POSTGRES_NEWS_EMBEDDING_ARTICLE_ID_COLUMN=article_id
POSTGRES_NEWS_EMBEDDING_COLUMN=embedding
POSTGRES_NEWS_CONTENT_COLUMN=content
POSTGRES_NEWS_TITLE_COLUMN=title
POSTGRES_NEWS_URL_COLUMN=url
POSTGRES_NEWS_PUBLISHED_AT_COLUMN=published_at
POSTGRES_NEWS_SOURCE_COLUMN=source
```

`CORP_BATCH_DSN`을 생략하면 `POSTGRES_NEWS_DSN`을 fallback으로 사용합니다. 주제/결과 저장 DB와 뉴스 검색 DB가 같으면 DSN을 하나만 써도 됩니다.

가장 중요한 값은 `EMBEDDING`입니다. 뉴스 DB에 저장된 vector를 만들 때 사용한 임베딩 모델과 GPT Researcher가 검색 질의를 임베딩할 때 사용하는 모델이 같아야 합니다.

## 5. Batch DB DDL

자동 생성은 다음 명령으로 할 수 있습니다.

```bash
cd /opt/gpt-researcher
source .venv/bin/activate
python -m gpt_researcher.corp.news_batch --init-db
```

수동으로 만들고 싶다면 아래 DDL을 사용합니다.

```sql
CREATE SCHEMA IF NOT EXISTS corp;

CREATE TABLE IF NOT EXISTS corp.research_topics (
    id bigserial PRIMARY KEY,
    topic text NOT NULL,
    prompt text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    report_type text NOT NULL DEFAULT 'research_report',
    tone text NOT NULL DEFAULT 'objective',
    next_run_at timestamptz,
    last_run_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS corp.research_reports (
    id bigserial PRIMARY KEY,
    topic_id bigint NOT NULL REFERENCES corp.research_topics(id),
    run_id uuid NOT NULL,
    status text NOT NULL,
    query text,
    report_markdown text,
    context_text text,
    source_urls jsonb NOT NULL DEFAULT '[]'::jsonb,
    research_sources jsonb NOT NULL DEFAULT '[]'::jsonb,
    costs jsonb NOT NULL DEFAULT '{}'::jsonb,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    error_message text,
    CONSTRAINT research_reports_status_check
        CHECK (status IN ('running', 'success', 'failed'))
);

CREATE UNIQUE INDEX IF NOT EXISTS corp_research_reports_run_id_idx
ON corp.research_reports (run_id);

CREATE INDEX IF NOT EXISTS corp_research_topics_due_idx
ON corp.research_topics (enabled, next_run_at, id);

CREATE INDEX IF NOT EXISTS corp_research_reports_running_idx
ON corp.research_reports (topic_id)
WHERE status = 'running';
```

배치 DB 계정은 `corp` schema에 대한 쓰기 권한이 필요합니다. 뉴스 DB 계정은 기사/임베딩 테이블에 대한 읽기 권한만 있으면 됩니다.

## 6. Topic 등록 방식

매일 실행할 주제를 `corp.research_topics`에 넣습니다.

```sql
INSERT INTO corp.research_topics (topic, prompt, report_type, tone)
VALUES (
    'daily semiconductor news',
    '오늘 수집된 반도체 산업 뉴스 중 투자, 공급망, 정책, 주요 기업 동향을 중심으로 요약 보고서를 작성해줘.',
    'research_report',
    'objective'
);
```

`enabled=true`이고 `next_run_at IS NULL`이면 cron이 실행될 때마다 대상이 됩니다. 반복 주기는 DB가 아니라 cron이 담당합니다. 특정 시점 이후에만 실행하려면 `next_run_at`을 미래 시간으로 넣고, 중지하려면 `enabled=false`로 바꿉니다.

동시 실행 방지는 두 단계로 처리합니다.

```text
DB 내부: FOR UPDATE SKIP LOCKED + running report row
cron 외부: flock
```

## 7. News DB 조건

뉴스 검색용 PostgreSQL에는 pgvector extension과 기사 본문/임베딩 테이블이 있어야 합니다.

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE news.articles (
    id bigint PRIMARY KEY,
    title text,
    url text,
    content text,
    published_at timestamptz,
    source text
);

CREATE TABLE news.article_embeddings (
    article_id bigint REFERENCES news.articles(id),
    embedding vector(1536)
);
```

실제 테이블명과 컬럼명이 다르면 `.env`의 `POSTGRES_NEWS_*` 값만 맞추면 됩니다. `vector(1536)`의 차원은 사내 임베딩 모델에 맞게 조정해야 합니다.

검색 성능을 위해 pgvector index를 권장합니다.

```sql
CREATE INDEX IF NOT EXISTS article_embeddings_embedding_hnsw
ON news.article_embeddings
USING hnsw (embedding vector_cosine_ops);
```

현재 retriever는 cosine distance 연산자인 `<=>`를 사용합니다.

최근 기사만 대상으로 삼고 싶으면 view를 만들어 `POSTGRES_NEWS_ARTICLES_TABLE`에 지정하는 방식이 운영상 단순합니다.

```sql
CREATE OR REPLACE VIEW news.recent_articles AS
SELECT *
FROM news.articles
WHERE published_at >= now() - interval '1 day';
```

```env
POSTGRES_NEWS_ARTICLES_TABLE=news.recent_articles
```

## 8. Smoke Test

DB 접속부터 확인합니다.

```bash
psql "host=10.x.x.x port=5432 dbname=research user=gptr_writer password=PASSWORD sslmode=require" -c "select 1"
psql "host=10.x.x.x port=5432 dbname=news user=gptr_reader password=PASSWORD sslmode=require" -c "select 1"
```

배치 schema를 만듭니다.

```bash
cd /opt/gpt-researcher
source .venv/bin/activate
python -m gpt_researcher.corp.news_batch --init-db
```

테스트 주제를 하나 넣고 1건만 실행합니다.

```sql
INSERT INTO corp.research_topics (topic, prompt)
VALUES ('smoke test', '오늘 주요 산업 뉴스를 5개 이내 핵심 bullet로 요약해줘.');
```

```bash
python -m gpt_researcher.corp.news_batch --limit 1 --verbose
```

결과 확인:

```sql
SELECT id, topic_id, status, started_at, finished_at, left(report_markdown, 200) AS preview
FROM corp.research_reports
ORDER BY id DESC
LIMIT 5;
```

## 9. Cron 실행

로그 디렉터리를 먼저 만듭니다.

```bash
mkdir -p /opt/gpt-researcher/logs
```

매일 오전 7시에 실행하는 예시입니다.

```cron
0 7 * * * cd /opt/gpt-researcher && flock -n /tmp/gptr-news-batch.lock bash -lc 'source .venv/bin/activate && python -m gpt_researcher.corp.news_batch --limit 10 >> logs/news_batch.log 2>&1'
```

`.env`는 Python 프로세스에서 자동 로드되지만, 사내 운영 표준상 shell에서 직접 로드해야 한다면 아래처럼 써도 됩니다.

```cron
0 7 * * * cd /opt/gpt-researcher && flock -n /tmp/gptr-news-batch.lock bash -lc 'source .venv/bin/activate && set -a && source .env && set +a && python -m gpt_researcher.corp.news_batch --limit 10 >> logs/news_batch.log 2>&1'
```

## 10. Docker를 쓸 때

배치 전용 운영이라면 Next.js 컨테이너는 올리지 않아도 됩니다.

```bash
docker compose build gpt-researcher
docker compose run --rm gpt-researcher python -m gpt_researcher.corp.news_batch --init-db
docker compose run --rm gpt-researcher python -m gpt_researcher.corp.news_batch --limit 10
```

컨테이너에서 DB IP와 사내 GPT API endpoint에 접근 가능한지 먼저 확인해야 합니다.

## 11. 운영 점검 SQL

최근 성공 보고서:

```sql
SELECT r.id, t.topic, r.status, r.started_at, r.finished_at
FROM corp.research_reports r
JOIN corp.research_topics t ON t.id = r.topic_id
ORDER BY r.id DESC
LIMIT 20;
```

실행 중으로 남은 row:

```sql
SELECT *
FROM corp.research_reports
WHERE status = 'running'
ORDER BY started_at;
```

오래 걸려 죽은 실행을 수동 실패 처리:

```sql
UPDATE corp.research_reports
SET status = 'failed',
    finished_at = now(),
    error_message = 'manual reset after stale running status'
WHERE status = 'running'
  AND started_at < now() - interval '6 hours';
```

## 12. 자주 나는 문제

### 실행 대상 topic이 없음

아래 조건을 확인합니다.

```sql
SELECT id, topic, enabled, next_run_at
FROM corp.research_topics
ORDER BY id;
```

`enabled=true`이고 `next_run_at IS NULL OR next_run_at <= now()`인 row만 실행됩니다. 같은 topic의 `running` report가 남아 있으면 다시 claim하지 않습니다.

### `CORP_BATCH_DSN is required`

`.env`가 없거나 `CORP_BATCH_DSN`, `POSTGRES_NEWS_DSN`이 모두 비어 있습니다.

### vector dimension 오류

뉴스 적재 배치에서 사용한 임베딩 모델과 `.env`의 `EMBEDDING` 모델이 다릅니다. 두 값을 반드시 동일한 사내 임베딩 모델로 맞춰야 합니다.

### Tavily API key 오류

회사용 설정에서는 Tavily를 사용하지 않습니다. `RETRIEVER=postgres_news`인지 확인하고, 잘못된 retriever 값이 Tavily fallback으로 넘어가지 않도록 현재 소스는 fail-closed로 동작합니다.

### 프론트 `authToken`이 필요한지

필요 없습니다. `authToken`은 프론트/LangGraph UI 실행 때 필요한 값이고, cron 배치에서는 `python -m gpt_researcher.corp.news_batch`만 실행합니다.

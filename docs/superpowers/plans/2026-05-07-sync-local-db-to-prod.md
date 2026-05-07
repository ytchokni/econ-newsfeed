# Sync Local Database to Production

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the production database with the local database, which is a strict superset (58,920 researchers / 62,373 papers / 236 feed events vs prod's 21,979 / 7,740 / 4).

**Architecture:** Full dump-and-restore. The databases share the same schema but IDs diverge above ~8100 for researchers, making incremental sync impractical. The local `html_content` table stores 2GB of raw HTML that doesn't need to transfer — only the metadata (hashes, timestamps) matters, since prod will re-fetch HTML on next scrape. All other tables transfer in full.

**Key constraints:**
- Prod DB container has 768MB memory limit — must temporarily increase for import
- Prod disk has 50GB free — plenty for a dump
- API must be stopped during restore to prevent writes
- Existing prod backup should be taken first as a safety net

**SSH:** `ssh -i ~/.ssh/LightsailDefaultKey-eu-central-1.pem ubuntu@18.195.185.188`
**Prod app dir:** `/opt/econ-newsfeed`
**Prod DB creds:** user `econ_app`, password `wobti1-Sofbiv-woqnym`, database `econ_newsfeed`
**Local DB creds:** user `econ_app`, password `secret`, database `econ_newsfeed`

---

### Task 1: Backup production database

Take a safety backup of the current prod database before any changes.

**Files:** None (remote commands only)

- [ ] **Step 1: SSH in and run manual backup**

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-central-1.pem ubuntu@18.195.185.188 \
  "cd /opt/econ-newsfeed && ./scripts/backup.sh"
```

Expected: Backup saved to `/backups/econ_newsfeed_YYYYMMDD_HHMMSS.sql.gz`

- [ ] **Step 2: Verify backup exists and is non-trivial**

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-central-1.pem ubuntu@18.195.185.188 \
  "ls -lh /backups/ | tail -3"
```

Expected: Latest backup file, ~50-100MB compressed.

---

### Task 2: Dump local database (excluding HTML bodies)

Dump all tables normally, except `html_content` where we exclude the 2GB `content` and `raw_html` columns. This reduces the dump from ~2.3GB to ~250MB.

**Files:** Creates `/tmp/econ_newsfeed_local_dump.sql.gz`

- [ ] **Step 1: Dump all tables except html_content**

```bash
docker compose exec -T db mysqldump -u econ_app -psecret econ_newsfeed \
  --single-transaction --routines --triggers \
  --ignore-table=econ_newsfeed.html_content \
  | gzip > /tmp/econ_newsfeed_local_dump_tables.sql.gz
```

Expected: Compressed dump ~100-200MB.

- [ ] **Step 2: Dump html_content structure only (no data)**

```bash
docker compose exec -T db mysqldump -u econ_app -psecret econ_newsfeed \
  html_content --no-data \
  > /tmp/econ_newsfeed_html_content_schema.sql
```

- [ ] **Step 3: Export html_content metadata (without content/raw_html columns)**

The `content` and `raw_html` columns are the 2GB of HTML bodies. We keep everything else: id, url_id, content_hash, timestamp, researcher_id, extracted_at, extracted_hash.

```bash
docker compose exec -T db mysql -u econ_app -psecret econ_newsfeed -N -e "
SELECT id, url_id,
  IFNULL(QUOTE(content_hash), 'NULL'),
  IFNULL(QUOTE(timestamp), 'NULL'),
  IFNULL(researcher_id, 'NULL'),
  IFNULL(QUOTE(extracted_at), 'NULL'),
  IFNULL(QUOTE(extracted_hash), 'NULL')
FROM html_content
" | awk -F'\t' '{
  printf "INSERT INTO html_content (id, url_id, content_hash, timestamp, researcher_id, extracted_at, extracted_hash) VALUES (%s, %s, %s, %s, %s, %s, %s);\n", $1, $2, $3, $4, $5, $6, $7
}' | gzip > /tmp/econ_newsfeed_html_content_data.sql.gz
```

- [ ] **Step 4: Combine into a single dump file**

```bash
{
  zcat /tmp/econ_newsfeed_local_dump_tables.sql.gz
  cat /tmp/econ_newsfeed_html_content_schema.sql
  zcat /tmp/econ_newsfeed_html_content_data.sql.gz
} | gzip > /tmp/econ_newsfeed_local_full.sql.gz
```

- [ ] **Step 5: Verify dump size and content**

```bash
ls -lh /tmp/econ_newsfeed_local_full.sql.gz
zcat /tmp/econ_newsfeed_local_full.sql.gz | head -20
zcat /tmp/econ_newsfeed_local_full.sql.gz | grep -c "INSERT INTO"
```

Expected: File ~100-200MB. Should contain INSERT statements for all tables.

---

### Task 3: Transfer dump to production

- [ ] **Step 1: SCP the dump to Lightsail**

```bash
scp -i ~/.ssh/LightsailDefaultKey-eu-central-1.pem \
  /tmp/econ_newsfeed_local_full.sql.gz \
  ubuntu@18.195.185.188:/tmp/econ_newsfeed_local_full.sql.gz
```

- [ ] **Step 2: Verify file arrived intact**

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-central-1.pem ubuntu@18.195.185.188 \
  "ls -lh /tmp/econ_newsfeed_local_full.sql.gz"
```

Expected: Same size as local file.

---

### Task 4: Restore on production

- [ ] **Step 1: Temporarily increase DB container memory to 1.5GB**

The DB container has a 768MB limit that can OOM during large imports.

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-central-1.pem ubuntu@18.195.185.188 \
  "docker update --memory 1500m econ-newsfeed-db-1"
```

- [ ] **Step 2: Stop the API container to prevent writes**

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-central-1.pem ubuntu@18.195.185.188 \
  "cd /opt/econ-newsfeed && docker compose stop api"
```

- [ ] **Step 3: Drop and recreate the database**

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-central-1.pem ubuntu@18.195.185.188 \
  "cd /opt/econ-newsfeed && docker compose exec -T db mysql -u root -p\$(grep MYSQL_ROOT_PASSWORD .env | cut -d= -f2) -e \"
    DROP DATABASE IF EXISTS econ_newsfeed;
    CREATE DATABASE econ_newsfeed CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
    GRANT ALL PRIVILEGES ON econ_newsfeed.* TO 'econ_app'@'%';
    FLUSH PRIVILEGES;
  \""
```

- [ ] **Step 4: Import the dump**

Pipe through `sed` to remove DEFINER clauses (the local user may differ from prod), then import in chunks to avoid OOM.

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-central-1.pem ubuntu@18.195.185.188 \
  "zcat /tmp/econ_newsfeed_local_full.sql.gz \
   | sed 's/DEFINER=[^ ]* / /g' \
   | docker compose -f /opt/econ-newsfeed/docker-compose.yml exec -T db \
       mysql -u econ_app -p'wobti1-Sofbiv-woqnym' econ_newsfeed"
```

This may take 5-15 minutes depending on network and disk speed. If it OOMs, the backup from Task 1 is the safety net.

- [ ] **Step 5: Verify row counts match local**

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-central-1.pem ubuntu@18.195.185.188 \
  "cd /opt/econ-newsfeed && docker compose exec -T db mysql -u econ_app -p'wobti1-Sofbiv-woqnym' econ_newsfeed -e \"
    SELECT 'researchers' AS tbl, COUNT(*) AS cnt FROM researchers
    UNION ALL SELECT 'papers', COUNT(*) FROM papers
    UNION ALL SELECT 'feed_events', COUNT(*) FROM feed_events
    UNION ALL SELECT 'html_content', COUNT(*) FROM html_content
    UNION ALL SELECT 'paper_links', COUNT(*) FROM paper_links
    UNION ALL SELECT 'authorship', COUNT(*) FROM authorship;
  \""
```

Expected counts:
- researchers: ~58,920
- papers: ~62,373
- feed_events: ~236
- html_content: ~17,277
- paper_links: ~25,402
- authorship: ~178,030

---

### Task 5: Post-restore cleanup

- [ ] **Step 1: Fix stuck scrape_log entries**

The local DB also has stuck `running` entries. Clean them up so the scheduler can run.

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-central-1.pem ubuntu@18.195.185.188 \
  "cd /opt/econ-newsfeed && docker compose exec -T db mysql -u econ_app -p'wobti1-Sofbiv-woqnym' econ_newsfeed -e \"
    UPDATE scrape_log
    SET status = 'failed',
        finished_at = NOW(),
        error_message = 'Cleaned up stale running entry during DB sync'
    WHERE status = 'running' AND finished_at IS NULL;
  \""
```

- [ ] **Step 2: Restore DB container memory limit**

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-central-1.pem ubuntu@18.195.185.188 \
  "docker update --memory 768m econ-newsfeed-db-1"
```

- [ ] **Step 3: Start the API container**

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-central-1.pem ubuntu@18.195.185.188 \
  "cd /opt/econ-newsfeed && docker compose start api"
```

- [ ] **Step 4: Verify API is healthy**

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-central-1.pem ubuntu@18.195.185.188 \
  "curl -s http://localhost:8000/api/publications?limit=3 | head -200"
```

Expected: JSON response with publication data.

- [ ] **Step 5: Clean up temp files**

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-central-1.pem ubuntu@18.195.185.188 \
  "rm /tmp/econ_newsfeed_local_full.sql.gz"
rm /tmp/econ_newsfeed_local_dump_tables.sql.gz \
   /tmp/econ_newsfeed_html_content_schema.sql \
   /tmp/econ_newsfeed_html_content_data.sql.gz \
   /tmp/econ_newsfeed_local_full.sql.gz
```

---

### Task 6: Verify the newsfeed works end-to-end

- [ ] **Step 1: Check the live frontend**

Open `https://econ-newsfeed.vercel.app` in a browser and verify:
- Newsfeed loads with feed events
- Researcher directory shows researchers
- A researcher detail page loads with papers

- [ ] **Step 2: Spot-check a known researcher**

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-central-1.pem ubuntu@18.195.185.188 \
  "cd /opt/econ-newsfeed && docker compose exec -T db mysql -u econ_app -p'wobti1-Sofbiv-woqnym' econ_newsfeed -e \"
    SELECT r.id, r.first_name, r.last_name, COUNT(a.paper_id) as papers
    FROM researchers r
    JOIN authorship a ON r.id = a.researcher_id
    WHERE r.id = 1
    GROUP BY r.id;
  \""
```

Expected: Max Friedrich Steinhardt with papers.

---

## Rollback

If anything goes wrong, restore from the prod backup taken in Task 1:

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-central-1.pem ubuntu@18.195.185.188 "
  cd /opt/econ-newsfeed
  docker compose stop api
  docker update --memory 1500m econ-newsfeed-db-1
  LATEST=\$(ls -t /backups/econ_newsfeed_*.sql.gz | head -1)
  zcat \$LATEST | docker compose exec -T db mysql -u root -p\$(grep MYSQL_ROOT_PASSWORD .env | cut -d= -f2) econ_newsfeed
  docker update --memory 768m econ-newsfeed-db-1
  docker compose start api
"
```

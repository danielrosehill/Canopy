-- Job definitions live in agent-data/config/jobs.json, so this table had no reader.
DROP TABLE system.jobs;
-- At most one unfinished run per job: this is the lock, and it holds across processes.
CREATE UNIQUE INDEX job_runs_one_running ON system.job_runs (job) WHERE finished IS NULL;
CREATE INDEX job_runs_latest ON system.job_runs (job, started DESC);
